# setup-winml.ps1 - isolated environments for the two EP routes (rule: never mix)
# venv-winml : onnxruntime-windowsml (Windows ML plugin-EP ORT) + winrt catalog
# venv-cpu   : PyPI onnxruntime (CPU baseline only, never for GPU claims)
param([string]$Repo = "$PSScriptRoot\..")
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path $Repo).Path
$env:UV_CACHE_DIR = 'C:\AIwork4me\.uvcache'
$winml = Join-Path $Repo 'venv-winml'
$cpu   = Join-Path $Repo 'venv-cpu'
uv venv $winml --python 3.12
uv pip install --python "$winml\Scripts\python.exe" onnxruntime-windowsml==1.30.1.202610302006 onnx numpy winrt-Microsoft.Windows.AI.MachineLearning
uv venv $cpu --python 3.12
uv pip install --python "$cpu\Scripts\python.exe" onnxruntime onnx numpy
& "$winml\Scripts\python.exe" -c "import onnxruntime as ort; print('winml ORT', ort.__version__)"
& "$cpu\Scripts\python.exe"   -c "import onnxruntime as ort; print('cpu ORT', ort.__version__)"
