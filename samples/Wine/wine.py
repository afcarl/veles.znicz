# -*-coding: utf-8 -*-
"""
.. invisible:
     _   _ _____ _     _____ _____
    | | | |  ___| |   |  ___/  ___|
    | | | | |__ | |   | |__ \ `--.
    | | | |  __|| |   |  __| `--. \
    \ \_/ / |___| |___| |___/\__/ /
     \___/\____/\_____|____/\____/

Created on August 4, 2013

Model created for class of wine recognition. Database - Wine.
Model - fully-connected Neural Network with SoftMax loss function.

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
import sys
from veles.downloader import Downloader

from veles.config import root
from veles.znicz.nn_units import NNSnapshotterToFile
import veles.znicz.nn_units as nn_units
import veles.znicz.all2all as all2all
import veles.znicz.decision as decision
import veles.znicz.evaluator as evaluator
import veles.znicz.gd as gd

sys.path.append(os.path.dirname(__file__))
from .loader_wine import WineLoader


root.common.disable.plotting = True

root.wine.update({
    "decision": {"fail_iterations": 200, "max_epochs": 100},
    "snapshotter": {"prefix": "wine", "time_interval": 1},
    "loader": {"minibatch_size": 10, "force_numpy": False},
    "learning_rate": 0.3,
    "weights_decay": 0.0,
    "layers": [8, 3]})


class WineWorkflow(nn_units.NNWorkflow):
    """Model created for class of wine recognition. Database - Wine.
    Model - fully-connected Neural Network with SoftMax loss function.
    """
    def __init__(self, workflow, **kwargs):
        super(WineWorkflow, self).__init__(workflow, **kwargs)
        layers = kwargs["layers"]

        self.downloader = Downloader(
            self, url=root.wine.downloader.url,
            directory=root.wine.downloader.directory,
            files=root.wine.downloader.files)
        self.downloader.link_from(self.start_point)

        self.repeater.link_from(self.downloader)

        self.loader = WineLoader(
            self, minibatch_size=root.wine.loader.minibatch_size,
            force_numpy=root.wine.loader.force_numpy,
            dataset_file=root.wine.loader.dataset_file,
            normalization_type=root.wine.loader.normalization_type)
        self.loader.link_from(self.repeater)

        # Add fwds units
        del self.forwards[:]
        for i, layer in enumerate(layers):
            if i < len(layers) - 1:
                aa = all2all.All2AllTanh(
                    self, output_sample_shape=(layer,),
                    weights_stddev=0.05, bias_stddev=0.05)
            else:
                aa = all2all.All2AllSoftmax(
                    self, output_sample_shape=(layer,),
                    weights_stddev=0.05, bias_stddev=0.05)
            self.forwards.append(aa)
            if i:
                self.forwards[-1].link_from(self.forwards[-2])
                self.forwards[-1].link_attrs(
                    self.forwards[-2], ("input", "output"))
            else:
                self.forwards[-1].link_from(self.loader)
                self.forwards[-1].link_attrs(
                    self.loader, ("input", "minibatch_data"))

        # Add evaluator for single minibatch
        self.evaluator = evaluator.EvaluatorSoftmax(self)
        self.evaluator.link_from(self.forwards[-1])
        self.evaluator.link_attrs(self.forwards[-1], "output", "max_idx")
        self.evaluator.link_attrs(self.loader,
                                  ("batch_size", "minibatch_size"),
                                  ("max_samples_per_epoch", "total_samples"),
                                  ("labels", "minibatch_labels"),
                                  ("offset", "minibatch_offset"),
                                  "class_lengths")

        # Add decision unit
        self.decision = decision.DecisionGD(
            self, fail_iterations=root.wine.decision.fail_iterations,
            max_epochs=root.wine.decision.max_epochs)
        self.decision.link_from(self.evaluator)
        self.decision.link_attrs(self.loader,
                                 "minibatch_class", "minibatch_size",
                                 "last_minibatch", "class_lengths",
                                 "epoch_ended", "epoch_number")
        self.decision.link_attrs(
            self.evaluator,
            ("minibatch_n_err", "n_err"),
            ("minibatch_confusion_matrix", "confusion_matrix"),
            ("minibatch_max_err_y_sum", "max_err_output_sum"))

        self.snapshotter = NNSnapshotterToFile(
            self, prefix=root.wine.snapshotter.prefix,
            directory=root.common.dirs.snapshots, compression="",
            interval=root.wine.snapshotter.interval,
            time_interval=root.wine.snapshotter.time_interval)
        self.snapshotter.link_from(self.decision)
        self.snapshotter.link_attrs(self.decision,
                                    ("suffix", "snapshot_suffix"))
        self.snapshotter.gate_skip = ~self.loader.epoch_ended
        self.snapshotter.skip = ~self.decision.improved

        self.end_point.link_from(self.snapshotter)
        self.end_point.gate_block = ~self.decision.complete

        # Add gradient descent units
        self.gds[:] = (None,) * len(self.forwards)
        self.gds[-1] = gd.GDSoftmax(self) \
            .link_from(self.snapshotter) \
            .link_attrs(self.evaluator, "err_output") \
            .link_attrs(self.forwards[-1], "output", "input",
                        "weights", "bias") \
            .link_attrs(self.loader, ("batch_size", "minibatch_size"))
        self.gds[-1].gate_skip = self.decision.gd_skip
        self.gds[-1].gate_block = self.decision.complete
        for i in range(len(self.forwards) - 2, -1, -1):
            self.gds[i] = gd.GDTanh(self) \
                .link_from(self.gds[i + 1]) \
                .link_attrs(self.gds[i + 1], ("err_output", "err_input")) \
                .link_attrs(self.forwards[i], "output", "input",
                            "weights", "bias") \
                .link_attrs(self.loader, ("batch_size", "minibatch_size"))
            self.gds[i].gate_skip = self.decision.gd_skip
        self.gds[0].need_err_input = False
        self.repeater.link_from(self.gds[0])
        self.loader.gate_block = self.decision.complete

    def initialize(self, learning_rate, weights_decay, device, **kwargs):
        super(WineWorkflow, self).initialize(learning_rate=learning_rate,
                                             weights_decay=weights_decay,
                                             device=device, **kwargs)


def run(load, main):
    load(WineWorkflow, layers=root.wine.layers)
    main(learning_rate=root.wine.learning_rate,
         weights_decay=root.wine.weights_decay)
