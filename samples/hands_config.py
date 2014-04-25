#!/usr/bin/python3.3 -O
"""
Created on Mart 26, 2014

Example of Mnist config.

Copyright (c) 2013 Samsung Electronics Co., Ltd.
"""


import os
from veles.config import root, Config

root.all2all = Config()  # not necessary for execution (it will do it in real
root.decision = Config()  # time any way) but good for Eclipse editor
root.loader = Config()

# optional parameters
train_dir = [os.path.join(root.common.test_dataset_root,
                          "hands/Positive/Training/*.raw"),
             os.path.join(root.common.test_dataset_root,
                          "hands/Negative/Training/*.raw")]
validation_dir = [os.path.join(root.common.test_dataset_root,
                               "hands/Positive/Testing/*.raw"),
                  os.path.join(root.common.test_dataset_root,
                               "hands/Negative/Testing/*.raw")]

root.update = {"decision": {"fail_iterations": 100,
                            "snapshot_prefix": "hands"},
               "loader": {"minibatch_maxsize": 60},
               "hands": {"global_alpha": 0.05,
                         "global_lambda": 0.0,
                         "layers": [30, 2],
                         "path_for_load_data": {"train": train_dir,
                                                "validation": validation_dir}}}
