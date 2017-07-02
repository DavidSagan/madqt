# encoding: utf-8
"""
UI for matching.
"""

from __future__ import absolute_import
from __future__ import unicode_literals

from pkg_resources import resource_filename
from collections import namedtuple
from math import sqrt

import numpy as np

from madqt.qt import QtGui, uic
from madqt.widget.tableview import ColumnInfo, ExtColumnInfo

import madqt.online.elements as elements
from madqt.util.collections import List
from madqt.util.enum import make_enum


MonitorItem = namedtuple('MonitorItem', ['proxy', 'envx', 'envy', 'tm'])
ResultItem = namedtuple('ResultItem', ['name', 'measured', 'model'])


def get_monitor_elem(widget, m):
    return widget.monitor_enum(m.proxy.name)

def set_monitor_elem(widget, m, i, name):
    if name is not None:
        p = widget.monitor_map[str(name)]
        v = p.dvm_backend.get()
        widget.monitors[i] = MonitorItem(p, v.get('widthx'), v.get('widthy'),
                                         widget.get_transfer_map(str(name)))


class EmittanceWidget(QtGui.QWidget):

    ui_file = 'emittance.ui'

    monitor_columns = [
        ExtColumnInfo("Monitor", get_monitor_elem, set_monitor_elem),
        ExtColumnInfo("Δx", 'envx'),
        ExtColumnInfo("Δy", 'envy'),
    ]

    result_columns = [
        ColumnInfo("Name", 'name'),
        ColumnInfo("Measured", 'measured'),
        ColumnInfo("Model", 'model'),
    ]

    def __init__(self, control):
        super(EmittanceWidget, self).__init__()
        uic.loadUi(resource_filename(__name__, self.ui_file), self)
        self.control = control

        monitors = list(control.iter_elements(elements.Monitor))
        self.monitor_map = {m.name: m for m in monitors}
        self.monitor_enum = make_enum('Monitor', [m.name for m in monitors])
        self.monitors = List()
        self.results = List()

        self.init_controls()
        self.set_initial_values()
        self.connect_signals()

    # The three steps of UI initialization

    def init_controls(self):
        self.mtab.horizontalHeader().setHighlightSections(False)
        self.rtab.horizontalHeader().setHighlightSections(False)
        self.mtab.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)
        self.rtab.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)
        self.mtab.setSelectionMode(QtGui.QAbstractItemView.ExtendedSelection)
        self.rtab.setSelectionMode(QtGui.QAbstractItemView.ExtendedSelection)
        self.mtab.set_columns(self.monitor_columns, self.monitors, self)
        self.rtab.set_columns(self.result_columns, self.results, self)

    def set_initial_values(self):
        pass

    def connect_signals(self):
        # update UI
        self.mtab.selectionChangedSignal.connect(self.selection_changed_monitor)
        self.monitors.update_after.connect(self.on_monitor_changed)
        # TODO: update UI: ok/export buttons
        # monitor actions
        self.button_remove_monitor.clicked.connect(self.mtab.removeSelectedRows)
        self.button_clear_monitor.clicked.connect(self.monitors.clear)
        self.button_add_monitor.clicked.connect(self.add_monitor)
        self.button_update_monitor.clicked.connect(self.update_monitor)
        # result actions
        self.button_ok.clicked.connect(self.accept)
        self.button_cancel.clicked.connect(self.reject)
        self.button_export.clicked.connect(self.export)

    def selection_changed_monitor(self):
        self.button_remove_monitor.setEnabled(bool(self.mtab.selectedIndexes()))

    def on_monitor_changed(self):
        self.button_clear_monitor.setEnabled(bool(self.monitors))
        self.button_update_monitor.setEnabled(bool(self.monitors))
        self.match_values()

    def accept(self):
        # TODO: use values
        self.window().accept()

    def reject(self):
        self.window().reject()

    def export(self):
        pass

    def add_monitor(self):
        name = self.monitor_enum._values[0]
        prox = self.monitor_map[name]
        vals = prox.dvm_backend.get()
        self.monitors.append(MonitorItem(
            prox, vals.get('widthx'), vals.get('widthy'),
            self.get_transfer_map(name)))

    def update_monitor(self):
        # reload values for all the monitors
        self.monitors[:] = [
            MonitorItem(m.proxy, v.get('widthx'), v.get('widthy'), m.tm)
            for m in self.monitors
            for v in [m.proxy.dvm_backend.get()]
        ]

    def get_transfer_map(self, dest):
        seg = self.control._segment
        return seg.get_transfer_map(seg.start, seg.get_element_info(dest))

    def match_values(self):

        if len(self.monitors) < 3:
            self.results[:] = []
            return

        seg = self.control._segment
        strip = seg.utool.strip_unit

        # TODO: when 'interpolate' is on, fix online control example values

        # TODO: when 'interpolate' is on -> choose correct element...?
        # -> not important for l=0 monitors

        envx = [strip('envx', m.envx) for m in self.monitors]
        envy = [strip('envy', m.envy) for m in self.monitors]
        tmx = [m.tm[0:2,0:2] for m in self.monitors]
        tmy = [m.tm[2:4,2:4] for m in self.monitors]

        # TODO: assert no coupling:
        # np.isclose(tm[0:2,2:4], 0)
        # np.isclose(tm[2:4,0:2], 0)

        # TODO: catch math domain error on individual planes, show the other
        # one if succeed
        ex, betx, alfx = self.calc_emit_one_plane(tmx, envx)
        ey, bety, alfy = self.calc_emit_one_plane(tmy, envy)

        beam = seg.sequence.beam
        twiss_args = seg.utool.dict_strip_unit(seg.twiss_args)

        self.results[:] = [
            ResultItem('betx', betx, twiss_args.get('betx')),
            ResultItem('bety', bety, twiss_args.get('bety')),
            ResultItem('alfx', alfx, twiss_args.get('alfx')),
            ResultItem('alfy', alfy, twiss_args.get('alfy')),
            ResultItem('ex',   ex,   beam['ex']),
            ResultItem('ey',   ey,   beam['ey']),
        ]


    def calc_emit_one_plane(self, transfer_matrices, widths):
        T = np.vstack([
            [M[0,0]**2, 2*M[0,0]*M[0,1], M[0,1]**2]
            for M in transfer_matrices
        ])
        W = np.array(widths)**2
        sigma, residuals, rank, singular = np.linalg.lstsq(T, W)
        b, a, c = sigma
        emit = sqrt(b*c - a*a)
        beta = b/emit
        alfa = a/emit * (-1)
        return (emit, beta, alfa)#, sum(residuals), (rank<len(x))
