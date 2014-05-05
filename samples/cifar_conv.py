#!/usr/bin/python3.3 -O
"""
Created on Mar 31, 2014

Cifar convolutional.

Copyright (c) 2013 Samsung Electronics Co., Ltd.
"""


import numpy
import os
import pickle

from veles.config import root
import veles.formats as formats
from veles.mutable import Bool
import veles.plotting_units as plotting_units
import veles.znicz.nn_units as nn_units
import veles.znicz.all2all as all2all
import veles.znicz.conv as conv
import veles.znicz.pooling as pooling
import veles.znicz.gd_conv as gd_conv
import veles.znicz.gd_pooling as gd_pooling
import veles.error as error
import veles.znicz.decision as decision
import veles.znicz.evaluator as evaluator
import veles.znicz.gd as gd
import veles.znicz.image_saver as image_saver
import veles.znicz.loader as loader
import veles.znicz.nn_plotting_units as nn_plotting_units

train_dir = os.path.join(root.common.test_dataset_root, "cifar/10")
validation_dir = os.path.join(root.common.test_dataset_root,
                              "cifar/10/test_batch")

root.defaults = {"decision": {"fail_iterations": 1000,
                              "snapshot_prefix": "cifar_conv",
                              "do_export_weights": True},
                 "loader": {"minibatch_maxsize": 100},
                 "image_saver": {"out_dirs":
                                 [os.path.join(root.common.cache_dir,
                                               "tmp/test"),
                                  os.path.join(root.common.cache_dir,
                                               "tmp/validation"),
                                  os.path.join(root.common.cache_dir,
                                               "tmp/train")]},
                 "weights_plotter": {"limit": 64},
                 "cifar_conv": {"global_alpha": 0.001,
                                "global_lambda": 0.004,
                                "layers":
                                [{"type": "conv", "n_kernels": 32,
                                  "kx": 5, "ky": 5, "padding": (2, 2, 2, 2)},
                                 {"type": "max_pooling",
                                  "kx": 3, "ky": 3, "sliding": (2, 2)},
                                 {"type": "conv", "n_kernels": 32,
                                  "kx": 5, "ky": 5, "padding": (2, 2, 2, 2)},
                                 {"type": "avg_pooling",
                                  "kx": 3, "ky": 3, "sliding": (2, 2)},
                                 {"type": "conv", "n_kernels": 64,
                                  "kx": 5, "ky": 5, "padding": (2, 2, 2, 2)},
                                 {"type": "avg_pooling",
                                  "kx": 3, "ky": 3, "sliding": (2, 2)}, 10],
                                "path_for_load_data": {"train": train_dir,
                                                       "validation":
                                                       validation_dir}}}


class Loader(loader.FullBatchLoader):
    """Loads Cifar dataset.
    """
    def load_data(self):
        """Here we will load data.
        """
        self.original_data = numpy.zeros([60000, 32, 32, 3],
                                         dtype=numpy.float32)
        self.original_labels = numpy.zeros(60000, dtype=numpy.int32)

        # Load Validation
        fin = open(root.cifar_conv.path_for_load_data.validation, "rb")
        u = pickle._Unpickler(fin)
        u.encoding = 'latin1'
        vle = u.load()
        fin.close()
        self.original_data[:10000] = formats.interleave(
            vle["data"].reshape(10000, 3, 32, 32))[:]
        self.original_labels[:10000] = vle["labels"][:]

        # Load Train
        for i in range(1, 6):
            fin = open(os.path.join(root.cifar_conv.path_for_load_data.train,
                                    ("data_batch_%d" % i)), "rb")
            u = pickle._Unpickler(fin)
            u.encoding = 'latin1'
            vle = u.load()
            fin.close()
            self.original_data[i * 10000: (i + 1) * 10000] = (
                formats.interleave(vle["data"].reshape(10000, 3, 32, 32))[:])
            self.original_labels[i * 10000: (i + 1) * 10000] = vle["labels"][:]

        self.class_samples[0] = 0
        self.nextclass_offsets[0] = 0
        self.class_samples[1] = 10000
        self.nextclass_offsets[1] = 10000
        self.class_samples[2] = 50000
        self.nextclass_offsets[2] = 60000

        self.total_samples = self.original_data.shape[0]

        for sample in self.original_data:
            formats.normalize(sample)


class Workflow(nn_units.NNWorkflow):
    """Cifar workflow.
    """
    def __init__(self, workflow, **kwargs):
        layers = kwargs.get("layers")
        device = kwargs.get("device")
        kwargs["layers"] = layers
        kwargs["device"] = device
        super(Workflow, self).__init__(workflow, **kwargs)

        self.repeater.link_from(self.start_point)

        self.loader = Loader(self)
        self.loader.link_from(self.repeater)

        # Add fwds units
        del self.fwds[:]
        for i in range(0, len(layers)):
            layer = layers[i]
            if type(layer) == int:
                if i == len(layers) - 1:
                    aa = all2all.All2AllSoftmax(self, output_shape=[layer],
                                                device=device)
                else:
                    aa = all2all.All2AllTanh(self, output_shape=[layer],
                                             device=device)
            elif type(layer) == dict:
                if layer["type"] == "conv":
                    aa = conv.ConvTanh(
                        self, n_kernels=layer["n_kernels"],
                        kx=layer["kx"], ky=layer["ky"],
                        sliding=layer.get("sliding", (1, 1, 1, 1)),
                        padding=layer.get("padding", (0, 0, 0, 0)),
                        device=device)
                elif layer["type"] == "max_pooling":
                    aa = pooling.MaxPooling(
                        self, kx=layer["kx"], ky=layer["ky"],
                        sliding=layer.get("sliding",
                                          (layer["kx"], layer["ky"])),
                        device=device)
                elif layer["type"] == "avg_pooling":
                    aa = pooling.AvgPooling(
                        self, kx=layer["kx"], ky=layer["ky"],
                        sliding=layer.get("sliding",
                                          (layer["kx"], layer["ky"])),
                        device=device)
                else:
                    raise error.ErrBadFormat(
                        "Unsupported layer type %s" % (layer["type"]))
            else:
                raise error.ErrBadFormat(
                    "layers element type should be int "
                    "for all-to-all or dictionary for "
                    "convolutional or pooling")
            self.fwds.append(aa)
            if i:
                self.fwds[-1].link_from(self.fwds[-2])
                self.fwds[-1].link_attrs(self.fwds[-2],
                                         ("input", "output"))
            else:
                self.fwds[-1].link_from(self.loader)
                self.fwds[-1].link_attrs(self.loader,
                                         ("input", "minibatch_data"))

        # Add Image Saver unit
        self.image_saver = image_saver.ImageSaver(
            self, out_dirs=root.image_saver.out_dirs)
        self.image_saver.link_from(self.fwds[-1])
        self.image_saver.link_attrs(self.fwds[-1], "output", "max_idx")
        self.image_saver.link_attrs(
            self.loader,
            ("input", "minibatch_data"),
            ("indexes", "minibatch_indexes"),
            ("labels", "minibatch_labels"),
            "minibatch_class", "minibatch_size")

        # Add evaluator for single minibatch
        self.evaluator = evaluator.EvaluatorSoftmax(self, device=device)
        self.evaluator.link_from(self.image_saver)
        self.evaluator.link_attrs(self.fwds[-1], ("y", "output"), "max_idx")
        self.evaluator.link_attrs(self.loader,
                                  ("batch_size", "minibatch_size"),
                                  ("labels", "minibatch_labels"),
                                  ("max_samples_per_epoch", "total_samples"))

        # Add decision unit
        self.decision = decision.Decision(
            self, fail_iterations=root.decision.fail_iterations,
            snapshot_prefix=root.decision.snapshot_prefix,
            do_export_weights=root.decision.do_export_weights)
        self.decision.link_from(self.evaluator)
        self.decision.link_attrs(self.loader,
                                 "minibatch_class",
                                 "no_more_minibatches_left",
                                 "class_samples")
        self.decision.link_attrs(
            self.evaluator,
            ("minibatch_n_err", "n_err"),
            ("minibatch_confusion_matrix", "confusion_matrix"))

        self.image_saver.gate_skip = ~self.decision.just_snapshotted
        self.image_saver.link_attrs(self.decision,
                                    ("this_save_time", "snapshot_time"))

        # Add gradient descent units
        del self.gds[:]
        self.gds.extend(list(None for i in range(0, len(self.fwds))))
        self.gds[-1] = gd.GDSM(self, device=device)
        self.gds[-1].link_from(self.decision)
        self.gds[-1].link_attrs(self.evaluator, "err_y")
        self.gds[-1].link_attrs(self.fwds[-1],
                                ("y", "output"),
                                ("h", "input"),
                                "weights", "bias")
        self.gds[-1].link_attrs(self.loader, ("batch_size", "minibatch_size"))
        self.gds[-1].gate_skip = self.decision.gd_skip
        for i in range(len(self.fwds) - 2, -1, -1):
            if isinstance(self.fwds[i], conv.ConvTanh):
                obj = gd_conv.GDTanhConv(
                    self, n_kernels=self.fwds[i].n_kernels,
                    kx=self.fwds[i].kx, ky=self.fwds[i].ky,
                    sliding=self.fwds[i].sliding,
                    padding=self.fwds[i].padding,
                    device=device)
            elif isinstance(self.fwds[i], pooling.MaxPooling):
                obj = gd_pooling.GDMaxPooling(
                    self, kx=self.fwds[i].kx, ky=self.fwds[i].ky,
                    sliding=self.fwds[i].sliding,
                    device=device)
                obj.link_attrs(self.fwds[i], ("h_offs", "input_offs"))
            elif isinstance(self.fwds[i], pooling.AvgPooling):
                obj = gd_pooling.GDAvgPooling(
                    self, kx=self.fwds[i].kx, ky=self.fwds[i].ky,
                    sliding=self.fwds[i].sliding,
                    device=device)
            elif isinstance(self.fwds[i], all2all.All2AllTanh):
                obj = gd.GDTanh(self, device=device)
            else:
                raise ValueError("Unsupported fwds unit type "
                                 " encountered: %s" %
                                 self.fwds[i].__class__.__name__)
            self.gds[i] = obj
            self.gds[i].link_from(self.gds[i + 1])
            self.gds[i].link_attrs(self.gds[i + 1], ("err_y", "err_h"))
            self.gds[i].link_attrs(self.fwds[i],
                                   ("y", "output"),
                                   ("h", "input"),
                                   "weights", "bias")
            self.gds[i].link_attrs(self.loader,
                                   ("batch_size", "minibatch_size"))
            self.gds[i].gate_skip = self.decision.gd_skip

        self.repeater.link_from(self.gds[0])

        self.end_point.link_from(self.decision)
        self.end_point.gate_block = ~self.decision.complete

        self.loader.gate_block = self.decision.complete

        # Error plotter
        self.plt = []
        styles = ["r-", "b-", "k-"]
        for i in range(1, 3):
            self.plt.append(plotting_units.AccumulatingPlotter(
                self, name="num errors", plot_style=styles[i]))
            self.plt[-1].link_attrs(self.decision, ("input", "epoch_n_err_pt"))
            self.plt[-1].input_field = i
            self.plt[-1].link_from(self.decision
                                   if len(self.plt) == 1 else self.plt[-2])
            self.plt[-1].gate_block = (~self.decision.epoch_ended
                                       if len(self.plt) == 1 else Bool(False))
        self.plt[0].clear_plot = True
        self.plt[-1].redraw_plot = True
        # Confusion matrix plotter
        """
        self.plt_mx = []
        for i in range(1, len(self.decision.confusion_matrixes)):
            self.plt_mx.append(plotting_units.MatrixPlotter(
                self, name=(("Test", "Validation", "Train")[i] + " matrix")))
            self.plt_mx[-1].link_attrs(self.decision,
                                       ("input", "confusion_matrixes"))
            self.plt_mx[-1].input_field = i
            self.plt_mx[-1].link_from(self.plt[-1])
            self.plt_mx[-1].gate_block = ~self.decision.epoch_ended
        """
        # Weights plotter
        self.decision.vectors_to_sync[self.gds[0].weights] = 1
        self.plt_mx = nn_plotting_units.Weights2D(
            self, name="First Layer Weights", limit=root.weights_plotter.limit)
        self.plt_mx.link_attrs(self.gds[0], ("input", "weights"))
        self.plt_mx.input_field = "v"
        self.plt_mx.get_shape_from = (
            [self.fwds[0].kx, self.fwds[0].ky]
            if isinstance(self.fwds[0], conv.Conv)
            else self.fwds[0].input)
        self.plt_mx.link_from(self.decision)
        self.plt_mx.gate_block = ~self.decision.epoch_ended

    def initialize(self, global_alpha, global_lambda, minibatch_maxsize,
                   device):
        super(Workflow, self).initialize(global_alpha=global_alpha,
                                         global_lambda=global_lambda,
                                         minibatch_maxsize=minibatch_maxsize,
                                         device=device)


def run(load, main):
    load(Workflow, layers=root.cifar_conv.layers)
    main(global_alpha=root.cifar_conv.global_alpha,
         global_lambda=root.cifar_conv.global_lambda,
         minibatch_maxsize=root.loader.minibatch_maxsize)
