"""The real split pyOpenMS strings must render configuration before Run controls."""
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_real_param_metadata_renders_widgets_and_preserves_run_controls(tmp_path):
    app = AppTest.from_string('''
from pathlib import Path
import pyopenms as poms
import streamlit as st
from src.workflow.ParameterManager import ParameterManager
from src.workflow.StreamlitUI import StreamlitUI

class FixtureParameters(ParameterManager):
    def create_ini(self, tool):
        path = self.ini_dir / (tool + '.ini')
        if not path.exists():
            param = poms.Param()
            param.setValue('Fixture:1:in', '', 'Input', ['input file'])
            param.setValue('Fixture:1:out', '', 'Output', ['output file'])
            param.setValue('Fixture:1:threads', 1, 'Threads')
            param.setValue('Fixture:1:algorithm:amount', 2, 'Amount')
            param.setValue('Fixture:1:algorithm:hidden', 3, 'Advanced', ['advanced'])
            param.setValue('Fixture:1:algorithm:mode', 'a', 'Mode')
            param.setValidStrings('Fixture:1:algorithm:mode', ['a', 'b'])
            param.setValue('Fixture:1:algorithm:ions', ['b'], 'Ions')
            param.setValidStrings('Fixture:1:algorithm:ions', ['b', 'y'])
            poms.ParamXMLFile().store(str(path), param)
        return True

st.session_state.setdefault('advanced', False)
manager = FixtureParameters(Path(st.session_state['workflow_dir']))
ui = StreamlitUI(manager.ini_dir.parent, None, None, manager)
ui.input_TOPP('Fixture', custom_defaults={'algorithm:amount': 7})
st.button('Run workflow')
''')
    app.session_state['workflow_dir'] = str(tmp_path)
    app.run()
    assert not app.exception and not app.error
    assert [(widget.label, widget.value) for widget in app.number_input] == [('amount', 7)]
    assert [(widget.label, widget.value) for widget in app.selectbox] == [('mode', 'a')]
    assert [(widget.label, widget.value) for widget in app.multiselect] == [('ions', ['b'])]
    assert not app.text_input  # Input/output files are handled by the upload UI.
    assert app.button[0].label == 'Run workflow'
    app.session_state['advanced'] = True
    app.run()
    assert not app.exception and not app.error
    assert {widget.label for widget in app.number_input} == {'amount', 'hidden'}
