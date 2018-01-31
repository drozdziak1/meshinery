#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
test_meshinery
----------------------------------

Tests for `meshinery` module.
"""

import unittest

import meshinery


class TestMeshinery(unittest.TestCase):

    def setUp(self):
        pass

    def test_something(self):
        assert(meshinery.__version__)

    def tearDown(self):
        pass
