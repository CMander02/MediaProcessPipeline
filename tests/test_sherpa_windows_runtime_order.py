import importlib.util
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL loader regression")
def test_sherpa_cuda_can_preload_before_onnxruntime_in_fresh_process():
    if importlib.util.find_spec("sherpa_onnx") is None:
        pytest.skip("sherpa-onnx is unavailable")
    if importlib.util.find_spec("onnxruntime") is None:
        pytest.skip("onnxruntime is unavailable")

    probe = """
from pathlib import Path
import tempfile

import sherpa_onnx

root = Path(tempfile.mkdtemp())
model = root / 'identity.onnx'
from onnx import TensorProto, helper, save
graph = helper.make_graph(
    [helper.make_node('Identity', ['input'], ['output'])],
    'identity',
    [helper.make_tensor_value_info('input', TensorProto.FLOAT, [1])],
    [helper.make_tensor_value_info('output', TensorProto.FLOAT, [1])],
)
onnx_model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 21)])
onnx_model.ir_version = 10
save(onnx_model, model)

import onnxruntime as ort
ort.InferenceSession(str(model), providers=['CPUExecutionProvider'])
print(sherpa_onnx.__version__)
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
