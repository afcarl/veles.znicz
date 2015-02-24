#!/usr/bin/python3 -O
"""
Created on Mart 21, 2014

Configuration file for Wine.
Model - fully-connected Neural Network with SoftMax loss function with RELU
activation.

Copyright (c) 2013 Samsung Electronics Co., Ltd.
"""


from veles.config import root


root.wine_relu.update({
    "decision": {"fail_iterations": 250, "max_epochs": 100000},
    "snapshotter": {"prefix": "wine_relu"},
    "loader": {"minibatch_size": 10, "force_cpu": False},
    "learning_rate": 0.03,
    "weights_decay": 0.0,
    "layers": [10, 3]})
