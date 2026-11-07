# Jarvis no Windows.
#   INSTALAR-JARVIS.bat  -> instala o que falta, liga tudo e abre no navegador (precisa de internet)
#   LIGAR-JARVIS.bat     -> so liga e abre (funciona sem internet)
# Pode rodar de novo quantas vezes quiser: so baixa o que ainda nao existe.
#
#   cerebro : Ollama + qwen2.5:3b         (~2 GB, uma vez)
#   voz     : Piper, voz en_GB "alan"     (~60 MB, uma vez; sem ela, o Jarvis usa a voz do Windows)
#   ouvidos : faster-whisper small + base (~600 MB, uma vez)
#
# Para desligar o Jarvis: feche a janela "Jarvis" que fica minimizada na barra de tarefas.
param([switch]$SoLigar)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"          # Invoke-WebRequest fica muito mais rapido
Set-Location -Path $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

function Step($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Warn($t) { Write-Host "AVISO: $t" -ForegroundColor Yellow }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}
function Test-Url($u) {
    try { Invoke-RestMethod -Uri $u -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}
function Brain-Up { Test-Url "http://localhost:11434/api/tags" }
function Jarvis-Up { Test-Url "http://127.0.0.1:4700/health" }

# Python: "py -3" (o lancador oficial) ou "python"
$script:PY = $null; $script:PYARGS = @()
function Find-Python {
    foreach ($c in @("py -3", "python")) {
        $parts = $c -split " "
        $exe = $parts[0]
        $extra = @($parts | Select-Object -Skip 1)
        if (-not (Get-Command $exe -CommandType Application -ErrorAction SilentlyContinue)) { continue }
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $ok = & $exe @extra -c "import sys; print(sys.version_info >= (3, 9))" 2>$null
        $ErrorActionPreference = $old
        if ($ok -eq "True") { $script:PY = $exe; $script:PYARGS = $extra; return $true }
    }
    return $false
}
function Invoke-Python { & $script:PY @script:PYARGS @args; if ($LASTEXITCODE -ne 0) { throw "python $args falhou" } }
function Need-Winget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "O winget nao esta neste Windows. Instale o 'App Installer' pela Microsoft Store e rode de novo."
    }
}
function Find-Ollama {
    if (Get-Command ollama -CommandType Application -ErrorAction SilentlyContinue) { return $true }
    if ($env:LOCALAPPDATA) {
        $guess = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
        if (Test-Path (Join-Path $guess "ollama.exe")) { $env:Path += ";$guess"; return $true }
    }
    return $false
}

try {
    # ----------------------------------------------------------------- Python
    Step "Python"
    if (-not (Find-Python)) {
        if ($SoLigar) { throw "Python nao encontrado. Rode INSTALAR-JARVIS.bat primeiro." }
        Need-Winget
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        Refresh-Path
        if (-not (Find-Python)) { throw "Python instalado, mas o Windows ainda nao o encontra. Feche esta janela e rode de novo." }
    }
    Invoke-Python -c "import sys; print('Python', sys.version.split()[0])"

    # ----------------------------------------------------------------- Ollama (o cerebro)
    Step "Ollama (o cerebro)"
    if (-not (Find-Ollama)) {
        if ($SoLigar) { throw "Ollama nao encontrado. Rode INSTALAR-JARVIS.bat primeiro." }
        Need-Winget
        winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
        Refresh-Path
        if (-not (Find-Ollama)) { throw "Ollama instalado, mas o Windows ainda nao o encontra. Feche esta janela e rode de novo." }
    }
    if (-not (Brain-Up)) {
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 60 -and -not (Brain-Up); $i++) { Start-Sleep -Seconds 1 }
        if (-not (Brain-Up)) { throw "O Ollama nao respondeu na porta 11434. Abra o app Ollama e rode de novo." }
    }
    $tag = (& $script:PY @script:PYARGS -c "import json; c=json.load(open('config.json', encoding='utf-8')); print(c['brains'][c.get('boot_brain','local')]['model'])").Trim()
    $have = (Invoke-RestMethod -Uri "http://localhost:11434/api/tags").models | ForEach-Object { $_.name }
    if ($have -notcontains $tag) {
        if ($SoLigar) { throw "O cerebro $tag nao foi baixado. Rode INSTALAR-JARVIS.bat primeiro." }
        Write-Host "baixando o cerebro $tag (so na primeira vez; uns 2 GB)..."
        ollama pull $tag
        if ($LASTEXITCODE -ne 0) { throw "ollama pull $tag falhou" }
    } else { Write-Host "cerebro $tag pronto" }

    if (-not $SoLigar) {
        # ------------------------------------------------------------- ouvidos e voz
        Step "Ouvidos (Whisper)"
        Invoke-Python -m pip install --quiet --disable-pip-version-check faster-whisper numpy
        Write-Host "baixando e aquecendo os modelos do Whisper (so na primeira vez)..."
        Invoke-Python hear.py

        Step "Voz (Piper)"
        try {
            Invoke-Python -m pip install --quiet --disable-pip-version-check piper-tts
            New-Item -ItemType Directory -Force -Path "models\piper" | Out-Null
            $hf = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium"
            foreach ($f in @("en_GB-alan-medium.onnx", "en_GB-alan-medium.onnx.json")) {
                $dest = "models\piper\$f"
                if (-not (Test-Path $dest) -or (Get-Item $dest).Length -eq 0) {
                    Write-Host "baixando $f"
                    Invoke-WebRequest -Uri "$hf/$f" -OutFile $dest
                }
            }
            Write-Host "voz pronta"
        } catch {
            Warn "a voz Piper nao instalou ($_). O Jarvis vai falar com a voz do Windows."
        }
    }

    # ----------------------------------------------------------------- liga o Jarvis
    Step "Ligando o Jarvis"
    if (-not (Jarvis-Up)) {
        $serverArgs = @($script:PYARGS) + @("server.py")
        Start-Process -FilePath $script:PY -ArgumentList $serverArgs -WorkingDirectory $PSScriptRoot -WindowStyle Minimized
        Write-Host "carregando cerebro, voz e ouvidos..."
        for ($i = 0; $i -lt 180 -and -not (Jarvis-Up); $i++) { Start-Sleep -Seconds 1 }
        if (-not (Jarvis-Up)) { throw "O Jarvis nao ligou. Abra a janela minimizada do Python para ver o erro." }
    }
    Write-Host "Jarvis no ar: http://127.0.0.1:4700/" -ForegroundColor Green
    Start-Process "http://127.0.0.1:4700/"

    if (-not $SoLigar) {
        Step "Conferindo tudo (preflight)"
        & $script:PY @script:PYARGS preflight.py
        Write-Host ""
        Write-Host "Pronto. Para ligar de novo depois, de dois cliques em LIGAR-JARVIS.bat." -ForegroundColor Green
    }
} catch {
    Write-Host ""
    Write-Host "ERRO: $_" -ForegroundColor Red
    Write-Host $_.InvocationInfo.PositionMessage -ForegroundColor DarkGray
    exit 1
}
