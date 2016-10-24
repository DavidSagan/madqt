# encoding: utf-8
"""
Shared base classes for different backends.
"""

from __future__ import absolute_import
from __future__ import unicode_literals

import os

from collections import namedtuple

import numpy as np

from six import string_types as basestring

from madqt.core.base import Object, Signal
from madqt.core.unit import UnitConverter, from_config
from madqt.resource.file import FileResource
from madqt.resource.package import PackageResource
from madqt.util.misc import cachedproperty


__all__ = [
    'ElementInfo',
    'FloorCoords',
    'minrpc_flags',
]


PlotInfo = namedtuple('PlotInfo', [
    'name',     # internal graph id (e.g. 'beta.g')
    'short',    # short display name (e.g. 'beta')
    'title',    # long display name ('Beta function')
    'curves',   # [CurveInfo]
])

CurveInfo = namedtuple('CurveInfo', [
    'name',     # internal curve id (e.g. 'beta.g.a')
    'short',    # display name for statusbar ('beta_a')
    'label',    # y-axis/legend label ('$\beta_a$')
    'style',    # **kwargs for ax.plot
    'unit',     # y unit
])


ElementInfo = namedtuple('ElementInfo', ['name', 'index', 'at'])
FloorCoords = namedtuple('FloorCoords', ['x', 'y', 'z', 'theta', 'phi', 'psi'])


class EngineBase(Object):

    """

    Abstract properties:

        backend             backend object
        backend_libname     name of the binding.
        backend_title       ui title of the backend accelerator code.
        segment
    """

    destroyed = Signal()

    def __init__(self, filename, app_config):
        super(EngineBase, self).__init__()
        self.app_config = app_config
        module = self.__class__.__module__.rsplit('.', 1)[-1]
        self.config = PackageResource('madqt.engine').yaml(module + '.yml')
        self.utool = UnitConverter.from_config_dict(self.config['units'])
        self.load(filename)

    def load(self, filename):
        """Load model or plain MAD-X file."""
        path, name = os.path.split(filename)
        self.repo = FileResource(path)
        ext = os.path.splitext(name)[1].lower()
        self.load_dispatch(name, ext)

    def load_dispatch(self, filename, ext):
        raise NotImplementedError

    def minrpc_flags(self):
        """Flags for launching the backend library in a remote process."""
        import subprocess
        return dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # stdin=None leads to an error on windows when STDIN is broken.
            # Therefore, we need set stdin=os.devnull by passing stdin=False:
            stdin=False,
            bufsize=0)

    def destroy(self):
        """Annihilate current universe. Stop interpreter."""
        if self.rpc_client:
            self.rpc_client.close()
        self.backend = None
        if self.segment is not None:
            self.segment.destroy()
        self.destroyed.emit()

    @property
    def rpc_client(self):
        """Low level RPC client."""
        return self.backend and self.backend._service

    @property
    def remote_process(self):
        """Backend process."""
        return self.backend and self.backend._process

    def _load_params(self, data, name):
        """Load parameter dict from file if necessary."""
        vals = data.get(name, {})
        if isinstance(vals, basestring):
            data[name] = self.repo.yaml(vals, encoding='utf-8')


class SegmentBase(Object):

    """
    Models a continuous section of the machine. This is represented in MAD-X
    as a `range` and in Bmad as a `branch`.
    """

    updated = Signal()
    destroyed = Signal()

    def destroy(self):
        self.universe.segment = None
        self.destroyed.emit()

    def elements(self):
        raise NotImplementedError

    def survey(self):
        raise NotImplementedError

    def survey_elements(self):
        raise NotImplementedError

    def get_twiss_args_raw(self, elem):
        raise NotImplementedError

    def get_element_data_raw(self, elem):
        raise NotImplementedError

    def get_element_index(self, elem):
        raise NotImplementedError

    @property
    def data(self):
        return {
            'sequence': self.sequence,
            'range': self.range,
            'beam': self.beam,
            'twiss': self.twiss_args,
        }

    @property
    def utool(self):
        return self.universe.utool

    def get_element_info(self, element):
        """Get :class:`ElementInfo` from element name or index."""
        if isinstance(element, ElementInfo):
            return element
        if isinstance(element, basestring):
            element = self.get_element_index(element)
        if element < 0:
            element += len(self.elements)
        element_data = self.get_element_data(element)
        return ElementInfo(element_data['name'], element, element_data['at'])

    def get_beam(self):
        return self.utool.dict_add_unit(self.get_beam_raw())

    def set_beam(self, beam):
        self.set_beam_raw(self.utool.dict_strip_unit(beam))

    def get_twiss_args(self):
        return self.utool.dict_add_unit(self.get_twiss_args_raw())

    def set_twiss_args(self, twiss):
        self.set_twiss_args_raw(self.utool.dict_strip_unit(twiss))

    beam = property(get_beam, set_beam)
    twiss_args = property(get_twiss_args, set_twiss_args)

    def get_element_data(self, index):
        return self.utool.dict_add_unit(self.get_element_data_raw(index))

    def get_element_by_position(self, pos):
        """Find optics element by longitudinal position."""
        if pos is None:
            return None
        for elem in self.elements:
            at, L = elem['at'], elem['l']
            if pos >= at and pos <= at+L:
                return elem
        return None

    def get_element_by_name(self, name):
        return self.elements[self.get_element_index(name)]

    # curves

    @property
    def curve_style(self):
        return self.universe.config['curve_style']

    @cachedproperty
    def builtin_graphs(self):
        return {
            info['short']: PlotInfo(
                name=info['name'],
                short=info['short'],
                title=info['title'],
                curves=[
                    CurveInfo(
                        name=curve['name'],
                        short=curve['short'],
                        label=curve['label'],
                        style=self.curve_style[curve_index],
                        unit=from_config(curve['unit']))
                    for curve_index, curve in enumerate(info['curves'])
                ])
            for info in self.universe.app_config['builtin_graphs']
        }

    @cachedproperty
    def native_graphs(self):
        return self.get_native_graphs()

    def get_graph_data(self, name):
        """
        Get the data for a particular graph as dict of numpy arrays.

        :rtype: PlotInfo
        """
        if name in self.native_graphs:
            return self.get_native_graph_data(name)
        info = self.builtin_graphs[name]
        if name == 'envelope':
            beta, data = self.get_native_graph_data('beta')
            emittances = self.ex(), self.ey()
            data = {
                env_i.name: np.hstack((
                    data[beta_i.name][:,[0]],
                    (data[beta_i.name][:,[1]] * emit)**0.5
                ))
                for beta_i, env_i, emit in zip(
                        beta.curves, info.curves, emittances)
            }
        else:
            raise ValueError("Unknown graph: {}".format(name))
        return info, data

    def get_graphs(self):
        graphs = {
            info.short: (name, info.title)
            for name, info in self.builtin_graphs.items()
        }
        graphs.update(self.native_graphs)
        return graphs

    def get_native_graph_data(self, name):
        """Get the data for a particular graph."""
        raise NotImplementedError

    def get_native_graphs(self):
        """Get a list of graph names."""
        raise NotImplementedError

    def retrack(self):
        raise NotImplementedError
