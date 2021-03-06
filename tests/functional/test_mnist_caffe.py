#!/usr/bin/env python3
# -*-coding: utf-8 -*-
"""
.. invisible:
     _   _ _____ _     _____ _____
    | | | |  ___| |   |  ___/  ___|
    | | | | |__ | |   | |__ \ `--.
    | | | |  __|| |   |  __| `--. \
    \ \_/ / |___| |___| |___/\__/ /
     \___/\____/\_____|____/\____/

Created on April 2, 2014

███████████████████████████████████████████████████████████████████████████████

Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.

███████████████████████████████████████████████████████████████████████████████
"""


import os

from veles.config import root
from veles.snapshotter import SnapshotterToFile
from veles.tests import timeout, multi_device
from veles.znicz.tests.functional import StandardTest
import veles.znicz.samples.MNIST.mnist as mnist_caffe


class TestMnistCaffe(StandardTest):
    @classmethod
    def setUpClass(cls):
        root.mnistr.lr_adjuster.lr_parameters = {
            "base_lr": 0.01, "gamma": 0.0001, "pow_ratio": 0.75}
        root.mnistr.lr_adjuster.bias_lr_parameters = {
            "base_lr": 0.01, "gamma": 0.0001, "pow_ratio": 0.75}

        root.mnistr.update({
            "loss_function": "softmax",
            "loader_name": "mnist_loader",
            "lr_adjuster": {"do": True, "lr_policy_name": "inv",
                            "bias_lr_policy_name": "inv"},
            "decision": {"fail_iterations": 100},
            "snapshotter": {"prefix": "mnist_caffe_test"},
            "loader": {"minibatch_size": 64, "force_numpy": False,
                       "normalization_type": "linear",
                       "data_path":
                       os.path.join(root.common.dirs.datasets, "MNIST")},
            "layers": [{"type": "conv",
                        "->": {"n_kernels": 20, "kx": 5, "ky": 5,
                               "sliding": (1, 1), "weights_filling": "uniform",
                               "bias_filling": "constant", "bias_stddev": 0},
                        "<-": {"learning_rate": 0.01,
                               "learning_rate_bias": 0.02,
                               "gradient_moment": 0.9,
                               "gradient_moment_bias": 0,
                               "weights_decay": 0.0005,
                               "weights_decay_bias": 0}},

                       {"type": "max_pooling",
                        "->": {"kx": 2, "ky": 2, "sliding": (2, 2)}},

                       {"type": "conv",
                        "->": {"n_kernels": 50, "kx": 5, "ky": 5,
                               "sliding": (1, 1), "weights_filling": "uniform",
                               "bias_filling": "constant", "bias_stddev": 0},
                        "<-": {"learning_rate": 0.01,
                               "learning_rate_bias": 0.02,
                               "gradient_moment": 0.9,
                               "gradient_moment_bias": 0,
                               "weights_decay": 0.0005,
                               "weights_decay_bias": 0.0}},

                       {"type": "max_pooling",
                        "->": {"kx": 2, "ky": 2, "sliding": (2, 2)}},

                       {"type": "all2all_relu",
                        "->": {"output_sample_shape": 500,
                               "weights_filling": "uniform",
                               "bias_filling": "constant", "bias_stddev": 0},
                        "<-": {"learning_rate": 0.01,
                               "learning_rate_bias": 0.02,
                               "gradient_moment": 0.9,
                               "gradient_moment_bias": 0,
                               "weights_decay": 0.0005,
                               "weights_decay_bias": 0.0}},

                       {"type": "softmax",
                        "->": {"output_sample_shape": 10,
                               "weights_filling": "uniform",
                               "bias_filling": "constant"},
                        "<-": {"learning_rate": 0.01,
                               "learning_rate_bias": 0.02,
                               "gradient_moment": 0.9,
                               "gradient_moment_bias": 0,
                               "weights_decay": 0.0005,
                               "weights_decay_bias": 0.0}}]})

    @timeout(900)
    @multi_device()
    def test_mnist_caffe(self):
        self.info("Will test mnist workflow with caffe config")

        workflow = mnist_caffe.MnistWorkflow(
            self.parent,
            decision_config=root.mnistr.decision,
            snapshotter_config=root.mnistr.snapshotter,
            loader_name=root.mnistr.loader_name,
            loader_config=root.mnistr.loader,
            layers=root.mnistr.layers,
            loss_function=root.mnistr.loss_function,
            lr_adjuster_config=root.mnistr.lr_adjuster)
        workflow.decision.max_epochs = 3
        workflow.snapshotter.time_interval = 0
        workflow.snapshotter.interval = 3
        self.assertEqual(workflow.evaluator.labels,
                         workflow.loader.minibatch_labels)
        workflow.initialize(device=self.device)
        self.assertEqual(workflow.evaluator.labels,
                         workflow.loader.minibatch_labels)
        workflow.run()
        self.assertIsNone(workflow.thread_pool.failure)
        file_name = workflow.snapshotter.destination

        err = workflow.decision.epoch_n_err[1]
        self.assertEqual(err, 135)
        self.assertEqual(3, workflow.loader.epoch_number)

        self.info("Will load workflow from %s", file_name)
        workflow_from_snapshot = SnapshotterToFile.import_(file_name)
        workflow_from_snapshot.workflow = self.parent
        self.assertTrue(workflow_from_snapshot.decision.epoch_ended)
        workflow_from_snapshot.decision.max_epochs = 5
        workflow_from_snapshot.decision.complete <<= False
        self.assertEqual(workflow_from_snapshot.evaluator.labels,
                         workflow_from_snapshot.loader.minibatch_labels)
        workflow_from_snapshot.initialize(device=self.device, snapshot=True)
        self.assertEqual(workflow_from_snapshot.evaluator.labels,
                         workflow_from_snapshot.loader.minibatch_labels)
        workflow_from_snapshot.run()
        self.assertIsNone(workflow_from_snapshot.thread_pool.failure)

        err = workflow_from_snapshot.decision.epoch_n_err[1]
        self.assertEqual(err, 94)
        self.assertEqual(5, workflow_from_snapshot.loader.epoch_number)
        self.info("All Ok")

if __name__ == "__main__":
    StandardTest.main()
