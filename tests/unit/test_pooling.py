"""
Created on Dec 4, 2013

Unit test for pooling layer forward propagation.

Copyright (c) 2013 Samsung Electronics Co., Ltd.
"""


import logging
import numpy
import unittest

from veles.config import root
import veles.formats as formats
import veles.opencl as opencl
import veles.opencl_types as opencl_types
import veles.znicz.gd_pooling as gd_pooling
import veles.znicz.pooling as pooling
from veles.tests.dummy_workflow import DummyWorkflow


class TestPooling(unittest.TestCase):
    def setUp(self):
        root.common.unit_test = True
        root.common.plotters_disabled = True
        self.device = opencl.Device()

    def tearDown(self):
        del self.device

    def test_max_pooling(self):
        logging.info("Will test max pooling layer forward propagation")

        inp = formats.Vector()
        dtype = opencl_types.dtypes[root.common.dtype]
        inp.v = numpy.array(
            [3, 4, 3, 1, -1, -2, 1, 3, 2, 3, 3, 0, 4, 1,
             (-2), 0, 4, 4, -2, 1, 3, -3, -3, 4, 1, -3, -2, -4,
             (-3), 2, -1, 4, 2, 0, -3, 3, 1, -3, -4, -3, 0, -3,
             (-1), 0, -2, 2, 2, -4, -1, -1, 0, -2, 1, 3, 1, 2,
             2, -2, 4, 0, -1, 0, 1, 0, 0, 3, -3, 3, -1, 1,
             4, 0, -1, -2, 3, 4, -4, -2, -4, 3, -2, -3, -1, -1,
             (-1), -3, 3, 3, -2, -1, 3, 2, -1, -2, 4, -1, 2, 4,
             (-2), -1, 1, 3, -2, -2, 0, -2, 0, 4, -1, -2, -2, -3,
             3, 2, -2, 3, 1, -3, -2, -1, 4, -2, 0, -3, -1, 2,
             2, -3, -1, -1, -3, -2, 2, 3, 0, -2, 1, 2, 0, -3,
             (-4), 1, -1, 2, -1, 0, 3, -2, 4, -3, 4, 4, 1, -4,
             0, -1, 1, 3, 0, 1, 3, 4, -3, 2, 4, 3, -1, 0,
             (-1), 0, 1, -2, -4, 0, -4, -4, 2, 3, 2, -3, 1, 1,
             1, -1, -4, 3, 1, -1, -3, -4, -4, 3, -1, -4, -1, 0,
             (-1), -3, 4, 1, 2, -1, -2, -3, 3, 1, 3, -3, 4, -2],
            dtype=dtype).reshape(3, 5, 7, 2)

        c = pooling.MaxPooling(DummyWorkflow(), kx=2, ky=2)
        c.input = inp

        c.initialize(device=self.device)
        c.run()

        c.output.map_read()  # get results back
        t = numpy.array([[[[4, 4], [3, 3], [3, 4], [4, -4]],
                          [[-3, 4], [-3, -4], [-4, -3], [1, -3]],
                          [[4, -2], [-1, 0], [-3, 3], [-1, 1]]],
                         [[[4, -3], [-4, 4], [-4, 3], [2, 4]],
                          [[3, 3], [-2, -3], [4, 4], [-2, -3]],
                          [[2, -3], [-3, 3], [1, -2], [0, -3]]],
                         [[[-4, 3], [3, 4], [4, 4], [1, -4]],
                          [[-4, 3], [-4, -4], [-4, -4], [1, 1]],
                          [[4, -3], [2, -3], [3, -3], [4, -2]]]],
                        dtype=dtype)
        max_diff = numpy.fabs(t.ravel() - c.output.v.ravel()).max()
        self.assertLess(max_diff, 0.0001,
                        "Result differs by %.6f" % (max_diff))

        c.input_offs.map_read()  # get results back
        t = numpy.array([[[[16, 1], [20, 7], [10, 23], [12, 27]],
                          [[28, 31], [34, 47], [38, 37], [54, 41]],
                          [[58, 57], [60, 61], [66, 65], [68, 69]]],
                         [[[70, 85], [76, 75], [78, 79], [96, 97]],
                          [[112, 101], [102, 117], [120, 107], [110, 111]],
                          [[126, 127], [130, 133], [136, 135], [138, 139]]],
                         [[[140, 157], [146, 161], [148, 151], [152, 153]],
                          [[184, 185], [172, 175], [190, 193], [180, 181]],
                          [[198, 197], [200, 203], [204, 207], [208, 209]]]],
                        dtype=numpy.int32)
        max_diff = numpy.fabs(t.ravel() - c.input_offs.v.ravel()).max()
        self.assertLess(max_diff, 0.0001,
                        "Result differs by %.6f" % (max_diff))

        logging.info("All Ok")

    def test_gd_max_pooling(self):
        logging.info("Will test max pooling layer gradient descent")

        inp = formats.Vector()
        dtype = opencl_types.dtypes[root.common.dtype]
        inp.v = numpy.array([[[3, 3, -1, 1, 2, 3, 4],
                              [-2, 4, -2, 3, -3, 1, -2],
                              [-3, -1, 2, -3, 1, -4, 0],
                              [-1, -2, 2, -1, 0, 1, 1],
                              [2, 4, -1, 1, 0, -3, -1]],
                             [[4, -1, 3, -4, -4, -2, -1],
                              [-1, 3, -2, 3, -1, 4, 2],
                              [-2, 1, -2, 0, 0, -1, -2],
                              [3, -2, 1, -2, 4, 0, -1],
                              [2, -1, -3, 2, 0, 1, 0]],
                             [[-4, -1, -1, 3, 4, 4, 1],
                              [0, 1, 0, 3, -3, 4, -1],
                              [-1, 1, -4, -4, 2, 2, 1],
                              [1, -4, 1, -3, -4, -1, -1],
                              [-1, 4, 2, -2, 3, 3, 4]]], dtype=dtype)
        inp.v = inp.v.reshape(3, 5, 7, 1)

        c = gd_pooling.GDMaxPooling(DummyWorkflow(), kx=2, ky=2,
                                    device=self.device)
        c.h = inp
        c.h_offs = formats.Vector()
        c.h_offs.v = numpy.array(
            [8, 10, 5, 6, 14, 17, 19, 27, 29, 30, 33, 34,
             35, 38, 39, 48, 56, 51, 60, 55, 63, 65, 68, 69,
             70, 73, 74, 76, 92, 86, 95, 90, 99, 100,
             102, 104], dtype=numpy.int32)
        c.err_y = formats.Vector()
        c.err_y.v = numpy.array([1, 3, 0.5, -4, 1, -2, -3, -1, -1, 3, -3, -0.5,
                                 4, -4, -0.3, -3, -1, -3, 2, -2, -4, 2, -1, -3,
                                 (-4), 2, 3, 2, -1, -1, -3, 4, -2, 2, 0.3, -4],
                                dtype=dtype)
        c.initialize()
        c.err_h.map_write()
        c.err_h.v[:] = 1.0e30
        c.run()

        c.err_h.map_read()  # get results back
        t = numpy.array([[[0, 0, 0, 0, 0, 0.5, -4],
                          [0, 1, 0, 3, 0, 0, 0],
                          [1, 0, 0, -2, 0, -3, 0],
                          [0, 0, 0, 0, 0, 0, -1],
                          [0, -1, 3, 0, 0, -3, -0.5]],
                         [[4, 0, 0, -4, -0.3, 0, 0],
                          [0, 0, 0, 0, 0, 0, -3],
                          [0, 0, -3, 0, 0, 0, -2],
                          [-1, 0, 0, 0, 2, 0, 0],
                          [-4, 0, 2, 0, 0, -1, -3]],
                         [[-4, 0, 0, 2, 3, 0, 2],
                          [0, 0, 0, 0, 0, 0, 0],
                          [0, 0, -1, 0, 0, 0, 4],
                          [0, -1, 0, 0, -3, 0, 0],
                          [0, -2, 2, 0, 0.3, 0, -4]]], dtype=dtype)
        max_diff = numpy.fabs(t.ravel() - c.err_h.v.ravel()).max()
        self.assertLess(max_diff, 0.0001,
                        "Result differs by %.6f" % (max_diff))

        logging.info("All Ok")

    def test_avg_pooling(self):
        logging.info("Will test avg pooling layer forward propagation")

        inp = formats.Vector()
        dtype = opencl_types.dtypes[root.common.dtype]
        inp.v = numpy.array(
            [3, 4, 3, 1, -1, -2, 1, 3, 2, 3, 3, 0, 4, 1,
             (-2), 0, 4, 4, -2, 1, 3, -3, -3, 4, 1, -3, -2, -4,
             (-3), 2, -1, 4, 2, 0, -3, 3, 1, -3, -4, -3, 0, -3,
             (-1), 0, -2, 2, 2, -4, -1, -1, 0, -2, 1, 3, 1, 2,
             2, -2, 4, 0, -1, 0, 1, 0, 0, 3, -3, 3, -1, 1,
             4, 0, -1, -2, 3, 4, -4, -2, -4, 3, -2, -3, -1, -1,
             (-1), -3, 3, 3, -2, -1, 3, 2, -1, -2, 4, -1, 2, 4,
             (-2), -1, 1, 3, -2, -2, 0, -2, 0, 4, -1, -2, -2, -3,
             3, 2, -2, 3, 1, -3, -2, -1, 4, -2, 0, -3, -1, 2,
             2, -3, -1, -1, -3, -2, 2, 3, 0, -2, 1, 2, 0, -3,
             (-4), 1, -1, 2, -1, 0, 3, -2, 4, -3, 4, 4, 1, -4,
             0, -1, 1, 3, 0, 1, 3, 4, -3, 2, 4, 3, -1, 0,
             (-1), 0, 1, -2, -4, 0, -4, -4, 2, 3, 2, -3, 1, 1,
             1, -1, -4, 3, 1, -1, -3, -4, -4, 3, -1, -4, -1, 0,
             (-1), -3, 4, 1, 2, -1, -2, -3, 3, 1, 3, -3, 4, -2],
            dtype=dtype).reshape(3, 5, 7, 2)

        c = pooling.AvgPooling(DummyWorkflow(), kx=2, ky=2, device=self.device)
        c.input = inp

        c.initialize()
        c.run()

        c.output.map_read()  # get results back
        t = numpy.array([
                        [[[2, 2.25], [0.25, -0.25], [0.75, 1], [1, -1.5]],
                         [[-1.75, 2], [0, -0.5], [-0.5, -1.25], [0.5, -0.5]],
                         [[3, -1], [0, 0], [-1.5, 3], [-1, 1]]],
                        [[[1.25, -0.5], [0, 0.75], [-0.75, -0.75], [0.5, 1.5]],
                         [[0, 1.75], [-0.75, -2], [0.75, -0.75], [-1.5, -0.5]],
                         [[0.5, -2], [-0.5, 0.5], [0.5, 0], [0, -3]]],
                        [[[-1, 1.25], [1.25, 0.75], [2.25, 1.5], [0, -2]],
                         [[-0.75, 0], [-2.5, -2.25], [-0.25, -0.25], [0, 0.5]],
                         [[1.5, -1], [0, -2], [3, -1], [4, -2]]]],
                        dtype=dtype)
        max_diff = numpy.fabs(t.ravel() - c.output.v.ravel()).max()
        self.assertLess(max_diff, 0.0001,
                        "Result differs by %.6f" % (max_diff))

        logging.info("All Ok")

    def test_gd_avg_pooling(self):
        logging.info("Will test avg pooling layer gradient descent")

        inp = formats.Vector()
        dtype = opencl_types.dtypes[root.common.dtype]
        inp.v = numpy.array([
            [[[3, 6], [3, 6], [-1, -2], [1, 2], [2, 4], [3, 6], [4, 8]],
             [[-2, -4], [4, 8], [-2, -4], [3, 6], [-3, -6], [1, 2], [-2, -4]],
             [[-3, -6], [-1, -2], [2, 4], [-3, -6], [1, 2], [-4, -8], [0, 0]],
             [[-1, -2], [-2, -4], [2, 4], [-1, -2], [0, 0], [1, 2], [1, 2]],
             [[2, 4], [4, 8], [-1, -2], [1, 2], [0, 0], [-3, -6], [-1, -2]]],
            [[[4, 8], [-1, -2], [3, 6], [-4, -8], [-4, -8], [-2, -4],
              [-1, -2]],
             [[-1, -2], [3, 6], [-2, -4], [3, 6], [-1, -2], [4, 8], [2, 4]],
             [[-2, -4], [1, 2], [-2, -4], [0, 0], [0, 0], [-1, -2], [-2, -4]],
             [[3, 6], [-2, -4], [1, 2], [-2, -4], [4, 8], [0, 0], [-1, -2]],
             [[2, 4], [-1, -2], [-3, -6], [2, 4], [0, 0], [1, 2], [0, 0]]],
            [[[-4, -8], [-1, -2], [-1, -2], [3, 6], [4, 8], [4, 8], [1, 2]],
             [[0, 0], [1, 2], [0, 0], [3, 6], [-3, -6], [4, 8], [-1, -2]],
             [[-1, -2], [1, 2], [-4, -8], [-4, -8], [2, 4], [2, 4], [1, 2]],
             [[1, 2], [-4, -8], [1, 2], [-3, -6], [-4, -8], [-1, -2],
              [-1, -2]],
             [[-1, -2], [4, 8], [2, 2], [-2, -4],
              [3, 6], [3, 6], [4, 8]]]], dtype=dtype)

        c = gd_pooling.GDAvgPooling(DummyWorkflow(), kx=2, ky=2,
                                    device=self.device)
        c.h = inp
        c.err_y = formats.Vector()
        c.err_y.v = numpy.array([[1, 2], [3, 6], [0.5, 1], [-4, -8],
                                 [1, 2], [-2, -4], [-3, -6], [-1, -2],
                                 [-1, -2], [3, 6], [-3, -6], [-0.5, -1],

                                 [4, 8], [-4, -8], [-0.3, -0.6], [-3, -6],
                                 [-1, -2], [-3, -6], [2, 4], [-2, -4],
                                 [-4, -8], [2, 4], [-1, -2], [-3, -6],

                                 [-4, -8], [2, 4], [3, 6], [2, 4],
                                 [-1, -2], [-1, -2], [-3, -6], [4, 8],
                                 [-2, -4], [2, 4], [0.3, 0.6], [-4, -8]],
                                dtype=dtype)
        c.initialize()
        c.err_h.map_write()
        c.err_h.v[:] = 1.0e30
        c.run()

        c.err_h.map_read()  # get results back
        t = numpy.array([
            [[[0.25, 0.5], [0.25, 0.5], [0.75, 1.5], [0.75, 1.5],
              [0.125, 0.25], [0.125, 0.25], [-2, -4]],
             [[0.25, 0.5], [0.25, 0.5], [0.75, 1.5], [0.75, 1.5],
              [0.125, 0.25], [0.125, 0.25], [-2, -4]],
             [[0.25, 0.5], [0.25, 0.5], [-0.5, -1], [-0.5, -1],
              [-0.75, -1.5], [-0.75, -1.5], [-0.5, -1]],
             [[0.25, 0.5], [0.25, 0.5], [-0.5, -1], [-0.5, -1],
              [-0.75, -1.5], [-0.75, -1.5], [-0.5, -1]],
             [[-0.5, -1], [-0.5, -1], [1.5, 3], [1.5, 3],
              [-1.5, -3], [-1.5, -3], [-0.5, -1]]],
            [[[1, 2], [1, 2], [-1, -2], [-1, -2],
              [-0.075, -0.15], [-0.075, -0.15], [-1.5, -3]],
             [[1, 2], [1, 2], [-1, -2], [-1, -2],
              [-0.075, -0.15], [-0.075, -0.15], [-1.5, -3]],
             [[-0.25, -0.5], [-0.25, -0.5], [-0.75, -1.5], [-0.75, -1.5],
              [0.5, 1], [0.5, 1], [-1, -2]],
             [[-0.25, -0.5], [-0.25, -0.5], [-0.75, -1.5], [-0.75, -1.5],
              [0.5, 1], [0.5, 1], [-1, -2]],
             [[-2, -4], [-2, -4], [1, 2], [1, 2],
              [-0.5, -1], [-0.5, -1], [-3, -6]]],
            [[[-1, -2], [-1, -2], [0.5, 1], [0.5, 1],
              [0.75, 1.5], [0.75, 1.5], [1, 2]],
             [[-1, -2], [-1, -2], [0.5, 1], [0.5, 1],
              [0.75, 1.5], [0.75, 1.5], [1, 2]],
             [[-0.25, -0.5], [-0.25, -0.5], [-0.25, -0.5], [-0.25, -0.5],
              [-0.75, -1.5], [-0.75, -1.5], [2, 4]],
             [[-0.25, -0.5], [-0.25, -0.5], [-0.25, -0.5], [-0.25, -0.5],
              [-0.75, -1.5], [-0.75, -1.5], [2, 4]],
             [[-1, -2], [-1, -2], [1, 2], [1, 2],
              [0.15, 0.3], [0.15, 0.3], [-4, -8]]]], dtype=dtype)
        max_diff = numpy.fabs(t.ravel() - c.err_h.v.ravel()).max()
        self.assertLess(max_diff, 0.0001,
                        "Result differs by %.6f" % (max_diff))

        logging.info("All Ok")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
