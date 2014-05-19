"""
Created on Nov 14, 2013

Gradient Descent for Convolutional Units.

Copyright (c) 2013 Samsung Electronics Co., Ltd.
"""


import logging
import numpy
from veles.external.prettytable import PrettyTable
import time

from veles.config import root
import veles.error as error
import veles.formats as formats
import veles.opencl_types as opencl_types
import veles.znicz.nn_units as nn_units


class GradientDescentConv(nn_units.GradientDescentBase):
    """Gradient Descent.

    Must be assigned before initialize():
        output
        input
        err_output
        weights
        bias
        batch_size

    Updates after run():
        err_input
        err_output
        weights
        bias

    Creates within initialize():
        err_input

    Attributes:
        krn_err_input_clear_: OpenCL kernel for setting err_input with zeros.
        krn_err_input_: OpenCL kernel for computing err_input.
        krn_weights_: OpenCL kernel for weights update.
        krn_err_output_: OpenCL kernel for err_output update.
        krn_bias_: OpenCL kernel for bias update.
        n_kernels: number of convolutional kernels.
        kx: kernel width.
        ky: kernel height.
    """
    def __init__(self, workflow, **kwargs):
        n_kernels = kwargs["n_kernels"]
        kx = kwargs["kx"]
        ky = kwargs["ky"]
        padding = kwargs.get("padding", (0, 0, 0, 0))  # Left Top Right Bottom
        sliding = kwargs.get("sliding", (1, 1))  # X Y
        kwargs["n_kernels"] = n_kernels
        kwargs["kx"] = kx
        kwargs["ky"] = ky
        kwargs["padding"] = padding
        kwargs["sliding"] = sliding
        super(GradientDescentConv, self).__init__(workflow, **kwargs)
        self.n_kernels = n_kernels
        self.kx = kx
        self.ky = ky
        self.padding = tuple(padding)
        self.sliding = tuple(sliding)
        self.cl_const = numpy.zeros(
            3, dtype=opencl_types.dtypes[root.common.dtype])
        self.reduce_size = 64

    def init_unpickled(self):
        super(GradientDescentConv, self).init_unpickled()
        self.cl_sources_["gradient_descent_conv.cl"] = {}
        self.krn_err_input_clear_ = None
        self.krn_err_input_ = None
        self.krn_weights_ = None
        self.krn_err_output_ = None
        self.krn_bias_ = None

    def initialize(self, **kwargs):
        super(GradientDescentConv, self).initialize(**kwargs)
        batch_size = self.input.mem.shape[0]
        sy = self.input.mem.shape[1]
        sx = self.input.mem.shape[2]
        n_channels = self.input.mem.size // (batch_size * sx * sy)
        n_weights = self.n_kernels * self.kx * self.ky * n_channels
        """
        sx_full = sx + self.padding[0] + self.padding[2]
        sy_full = sy + self.padding[1] + self.padding[3]
        kernel_applies_per_width = (sx_full - self.kx) / self.sliding[0] + 1
        kernel_applies_per_height = (sy_full - self.ky) / self.sliding[1] + 1
        self._batch_multiplier = (kernel_applies_per_width *
                                  kernel_applies_per_height)
        """
        self._batch_multiplier = 1
        if self.weights.mem.size != n_weights:
            raise error.ErrBadFormat(
                "Expected number of weights to match "
                "input, n_kernels, kx, ky parameters")
        if self.bias.mem.size != self.n_kernels:
            raise error.ErrBadFormat("Expected bias to match n_kernels")
        if self.input.mem.size != batch_size * sy * sx * n_channels:
            raise error.ErrBadFormat(
                "Expected input size to match "
                "batch_size * sy * sx * n_channels")

        if (self.err_input.mem is None or
                self.err_input.mem.size != self.input.mem.size):
            self.err_input.reset()
            self.err_input.mem = numpy.zeros(self.input.mem.shape,
                                           dtype=self.err_output.mem.dtype)

        if (self.store_gradient and
                (self.gradient_weights.mem is None or
                 self.gradient_weights.mem.size != self.weights.mem.size)):
            self.gradient_weights.reset()
            self.gradient_weights.mem = numpy.zeros_like(self.weights.mem)

        if (self.store_gradient and
                (self.gradient_bias.mem is None or
                 self.gradient_bias.mem.size != self.bias.mem.size)):
            self.gradient_bias.reset()
            self.gradient_bias.mem = numpy.zeros_like(self.bias.mem)

        self.weights.initialize(self.device)
        self.bias.initialize(self.device)
        self.output.initialize(self.device)
        self.input.initialize(self.device)
        self.err_output.initialize(self.device)
        self.err_input.initialize(self.device)
        if self.store_gradient:
            self.gradient_weights.initialize(self.device)
            self.gradient_bias.initialize(self.device)

        if self.device is None:
            return

        if self.program_ is None:
            block_size = self.device.device_info.BLOCK_SIZE[
                opencl_types.numpy_dtype_to_opencl(self.err_output.mem.dtype)]
            self.reduce_size = min(self.reduce_size,
                                   self.kx * self.ky * n_channels)

            defines = {
                'USE_ATOMICS': 1,
                'BLOCK_SIZE': block_size,
                'BATCH': batch_size,
                'SX': sx,
                'SY': sy,
                'N_CHANNELS': n_channels,
                'KX': self.kx,
                'KY': self.ky,
                'N_KERNELS': self.n_kernels,
                'PAD_LEFT': self.padding[0],
                'PAD_TOP': self.padding[1],
                'PAD_RIGHT': self.padding[2],
                'PAD_BOTTOM': self.padding[3],
                'SLIDE_X': self.sliding[0],
                'SLIDE_Y': self.sliding[1],
                'REDUCE_SIZE': self.reduce_size
            }
            if self.apply_gradient:
                defines['APPLY_GRADIENT'] = 1
            if self.weights_transposed:
                defines['WEIGHTS_TRANSPOSED'] = 1
            if self.store_gradient:
                defines['STORE_GRADIENT'] = 1
            self.build_program(defines, "%s/gd_conv_%d_%d.cl" % (
                root.common.cache_dir,
                self.input.mem.size // self.input.mem.shape[0],
                self.output.mem.size // self.output.mem.shape[0]),
                dtype=self.err_output.mem.dtype)

            self.krn_err_input_clear_ = self.get_kernel("array_clear")
            self.krn_err_input_clear_.set_arg(0, self.err_input.devmem)

            self.krn_err_input_ = self.get_kernel("err_h_update")
            self.krn_err_input_.set_args(self.err_output.devmem, self.weights.devmem,
                                         self.err_input.devmem)

            self.krn_weights_ = self.get_kernel("weights_update")
            self.krn_weights_.set_args(self.err_output.devmem, self.input.devmem,
                                       self.weights.devmem)
            self.krn_weights_.set_arg(3, self.gradient_weights.devmem)

            self.krn_bias_ = self.get_kernel("bias_update")
            self.krn_bias_.set_args(self.err_output.devmem, self.bias.devmem)
            self.krn_bias_.set_arg(2, self.gradient_bias.devmem)

    def gpu_weights_update(self):
        self.input.unmap()
        self.err_output.unmap()
        self.weights.unmap()
        self.bias.unmap()
        self.gradient_weights.unmap()
        self.gradient_bias.unmap()

        batch_size = self.batch_size or self.output.mem.shape[0]
        batch_size *= self._batch_multiplier
        sy = self.input.mem.shape[1]
        sx = self.input.mem.shape[2]
        n_channels = self.input.mem.size // (self.input.mem.shape[0] * sx * sy)

        # weights
        alpha_batch = -self.learning_rate / batch_size
        alpha_lambda = -self.learning_rate * self.weights_decay

        self.cl_const[0] = alpha_batch
        self.cl_const[1] = alpha_lambda
        self.cl_const[2] = self.gradient_moment
        self.krn_weights_.set_args(None, None, None, None, self.cl_const[0:1],
                                   self.cl_const[1:2], self.cl_const[2:3])
        block_size = self.device.device_info.BLOCK_SIZE[
            opencl_types.numpy_dtype_to_opencl(self.err_output.mem.dtype)]
        if self.weights_transposed:
            global_size = [
                formats.roundup(self.n_kernels, block_size),
                formats.roundup(self.kx * self.ky * n_channels,
                                block_size)]
        else:
            global_size = [
                formats.roundup(self.kx * self.ky * n_channels,
                                block_size),
                formats.roundup(self.n_kernels, block_size)]
        local_size = [block_size, block_size]
        ev1 = self.execute_kernel(global_size, local_size, self.krn_weights_)

        # bias
        alpha_batch = -self.learning_rate_bias / batch_size
        alpha_lambda = -self.learning_rate_bias * self.weights_decay_bias

        self.cl_const[0] = alpha_batch
        self.cl_const[1] = alpha_lambda
        self.cl_const[2] = self.gradient_moment_bias
        self.krn_bias_.set_args(None, None, None, self.cl_const[0:1],
                                self.cl_const[1:2], self.cl_const[2:3])
        global_size = [self.n_kernels * self.reduce_size]
        local_size = [self.reduce_size]
        ev2 = self.execute_kernel(global_size, local_size, self.krn_bias_)

        ev1.wait()
        ev2.wait()

    def cpu_weights_update(self):
        self.input.map_read()
        self.err_output.map_read()
        self.weights.map_write()
        self.bias.map_write()
        self.gradient_weights.map_write()
        self.gradient_bias.map_write()

        batch_size = self.batch_size or self.output.mem.shape[0]
        sy = self.input.mem.shape[1]
        sx = self.input.mem.shape[2]
        n_channels = self.input.mem.size // (self.input.mem.shape[0] * sx * sy)

        sx_full = self.padding[0] + sx + self.padding[2]
        sy_full = self.padding[1] + sy + self.padding[3]
        nx = (sx_full - self.kx) // self.sliding[0] + 1
        ny = (sy_full - self.ky) // self.sliding[1] + 1
        sample_shape = (nx * ny, self.kx * self.ky * n_channels)

        # calculate gradient for weights
        gd_weights = numpy.zeros(self.weights.mem.shape)
        for batch in range(batch_size):
            # input data unrolling
            sample = numpy.empty(sample_shape)
            for by, bx in ((by, bx) for by in range(ny) for bx in range(nx)):
                y1, y2 = (by * self.sliding[1],
                          by * self.sliding[1] + self.ky)
                x1, x2 = (bx * self.sliding[0],
                          bx * self.sliding[0] + self.kx)
                i1, i2 = (min(max(y1 - self.padding[1], 0), sy),
                          min(max(y2 - self.padding[1], 0), sy))
                j1, j2 = (min(max(x1 - self.padding[0], 0), sx),
                          min(max(x2 - self.padding[0], 0), sx))
                cut = numpy.zeros((self.ky, self.kx, n_channels),
                                  dtype=self.input.mem.dtype)
                cut[(i1 - y1):(i2 - y1), (j1 - x1):(j2 - x1)] = \
                    self.input.mem[batch, i1:i2, j1:j2].reshape(cut.shape)
                sample[by * nx + bx] = cut.ravel()
            # TODO: consider case of transposed weights
            gd_weights += numpy.dot(self.err_output.mem[batch].transpose(),
                                    sample)

        # update weights
        alpha_batch = -self.learning_rate / batch_size
        alpha_lambda = -self.learning_rate * self.weights_decay
        gd_weights_reg = (gd_weights * alpha_batch +
                          self.weights.mem * alpha_lambda)
        if self.store_gradient:
            gd_weights_reg += self.gradient_weights.mem * self.gradient_moment
            self.gradient_weights.mem[:] = gd_weights_reg[:]
        if self.apply_gradient:
            self.weights.mem += gd_weights_reg

        # calculate gradient for bias
        gd_bias = numpy.zeros(self.bias.mem.shape)
        for batch in range(batch_size):
            gd_bias += numpy.add.reduce(self.err_output.mem[batch])
        # update bias
        alpha_batch = -self.learning_rate_bias / batch_size
        alpha_lambda = -self.learning_rate_bias * self.weights_decay_bias
        gd_bias_reg = gd_bias * alpha_batch + self.bias.mem * alpha_lambda
        if self.store_gradient:
            gd_bias_reg += self.gradient_bias.mem * self.gradient_moment_bias
            self.gradient_bias.mem[:] = gd_bias_reg[:]
        if self.apply_gradient:
            self.bias.mem += gd_bias_reg

    def gpu_err_input_update(self):
        """Backpropagate error (will compute err_input).
        """
        self.err_input.unmap()
        self.err_output.unmap()
        self.weights.unmap()

        # Clear the resulting matrix
        self.execute_kernel([self.err_input.mem.size], None,
                            self.krn_err_input_clear_).wait()

        batch_size = self.input.mem.shape[0]
        sy = self.input.mem.shape[1]
        sx = self.input.mem.shape[2]
        n_channels = self.input.mem.size // (batch_size * sx * sy)
        block_size = self.device.device_info.BLOCK_SIZE[
            opencl_types.numpy_dtype_to_opencl(self.err_output.mem.dtype)]
        kernel_size = self.kx * self.ky * n_channels
        global_size = [
            formats.roundup(kernel_size, block_size),
            formats.roundup(
                batch_size *
                ((sx + self.padding[0] + self.padding[2] - self.kx) //
                 self.sliding[0] + 1) *
                ((sy + self.padding[1] + self.padding[3] - self.ky) //
                 self.sliding[1] + 1),
                block_size)]
        local_size = [block_size, block_size]
        event = self.execute_kernel(global_size, local_size,
                                    self.krn_err_input_)
        event.wait()

    def print_debug_data(self, t_start):
        """
        Show weights statistics
        """
        if not self.log.isEnabledFor(logging.DEBUG):
            return
        self.weights.map_read()
        self.bias.map_read()
        self.gradient_bias.map_read()
        self.gradient_weights.map_read()
        weights = self.weights.mem
        bias = self.bias.mem
        grad_weights = self.gradient_weights.mem
        grad_bias = self.gradient_bias.mem

        n_input = self.input.mem.size // self.input.mem.shape[0]
        n_output = self.output.mem.size // self.output.mem.shape[0]
        delta_time = time.time() - t_start

        stats_table = PrettyTable("n_input", "n_output", "time")
        stats_table.float_format = ".3"
        stats_table.add_row(n_input, n_output, delta_time)
        self.debug("\n" + stats_table.get_string())

        weight_table = PrettyTable("TYPE", "Mean", "StdDev", "Min", "Max")
        weight_table.float_format = ".10"
        for (w_name, w_array) in [("Weight", weights), ("Bias", bias),
                                  ("Grad Weight", grad_weights),
                                  ("Grad Bias", grad_bias)]:
            w_mean = numpy.mean(w_array)
            w_stddev = numpy.std(w_array)
            w_min = numpy.min(w_array)
            w_max = numpy.max(w_array)
            weight_table.add_row(w_name, w_mean, w_stddev, w_min, w_max)
        self.debug("\n" + weight_table.get_string())

    def gpu_err_output_update(self):
        """Multiply err_output by activation derivative by output.
        """
        if self.krn_err_output_ is None:
            return
        self.output.unmap()
        self.err_output.unmap()
        ev = self.execute_kernel([self.err_output.mem.size], None,
                                 self.krn_err_output_)
        ev.wait()

    def ocl_run(self):
        """Do gradient descent.
        """
        t1 = time.time()
        self.gpu_err_output_update()
        self.gpu_err_input_update()
        self.gpu_weights_update()
        self.print_debug_data(t1)

    def cpu_run(self):
        raise error.ErrNotImplemented()


class GDTanhConv(GradientDescentConv):
    """Gradient Descent for f(x) = 1.7159 * tanh(0.6666 * s), s = (W * x + b),
       y = a * tanh(b * s).

    f'(s) = (a * tanh(b * s))' = a * tanh'(b * s) * b
          = a * (1.0 - tanh^2(b * s)) * b
          = a * b - a * b * tanh^2(b * s)
          = a * b - y * y * b / a
          = y * y * (-b / a) + (a * b)
          = y * y * (-0.388484177) + 1.14381894
    """
    def cpu_err_output_update(self):
        """Multiply err_output by activation derivative by s
           in terms of output.
        """
        self.output.map_read()
        self.err_output.map_write()
        output = self.output.mem
        self.err_output.mem *= output * output * (-0.388484177) + 1.14381894

    def initialize(self, **kwargs):
        self.cl_sources_["gradient_descent_tanh.cl"] = {}
        super(GDTanhConv, self).initialize(**kwargs)
        if self.device is None:
            return
        self.krn_err_output_ = self.get_kernel("err_y_update")
        self.krn_err_output_.set_args(self.err_output.devmem, self.output.devmem)


class GDRELUConv(GradientDescentConv):
    """Gradient Descent for f(x) = log(1.0 + exp(s)), s = (W * x + b),
       y = log(1.0 + exp(s)).

    f'(s) = 1.0 / (1.0 + exp(-s)) = 1.0 - exp(-y)
    """
    def cpu_err_output_update(self):
        """Multiply err_output by activation derivative by s
        in terms of output.
        """
        self.output.map_read()
        self.err_output.map_write()
        output = self.output.mem
        self.err_output.mem *= 1.0 - numpy.exp(-output)

    def initialize(self, **kwargs):
        self.cl_sources_["gradient_descent_relu.cl"] = {}
        super(GDRELUConv, self).initialize(**kwargs)
        if self.device is None:
            return
        self.krn_err_output_ = self.get_kernel("err_y_update")
        self.krn_err_output_.set_args(self.err_output.devmem, self.output.devmem)
