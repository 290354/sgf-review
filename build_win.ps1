# SGF Review · Windows 绿色版一键构建
# 在仓库根目录运行:  powershell -ExecutionPolicy Bypass -File build_win.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "==> [1/5] 安装依赖" -ForegroundColor Cyan
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install "pyinstaller>=6,<7"

Write-Host "==> [2/5] 生成图标" -ForegroundColor Cyan
python tools/make_icon.py

Write-Host "==> [3/5] 打包 exe" -ForegroundColor Cyan
pyinstaller --noconfirm --clean sgf-review.spec

Write-Host "==> [4/5] 下载 ffmpeg 静态构建" -ForegroundColor Cyan
$zip = Join-Path $root "ffmpeg_download.zip"
$ffdir = Join-Path $root "ffmpeg_tmp"
Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" `
                  -OutFile $zip -UseBasicParsing
Expand-Archive -Path $zip -DestinationPath $ffdir -Force
$ffmpeg = Get-ChildItem -Path $ffdir -Filter ffmpeg.exe -Recurse |
          Select-Object -First 1
if (-not $ffmpeg) { throw "解压后未找到 ffmpeg.exe" }

Write-Host "==> [5/5] 组装发布包" -ForegroundColor Cyan
$bundle = Join-Path $root "dist\sgf-review-win64"
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
Copy-Item "dist\sgf-review.exe" $bundle
Copy-Item $ffmpeg.FullName (Join-Path $bundle "ffmpeg.exe")
Copy-Item "README.md" $bundle
Copy-Item "RELEASE_NOTES.md" (Join-Path $bundle "更新说明.md")
Copy-Item "assets\README_WIN.txt" (Join-Path $bundle "使用说明.txt")
Copy-Item -Recurse "examples" $bundle

Write-Host "==> 冒烟自检" -ForegroundColor Cyan
& (Join-Path $bundle "sgf-review.exe") --cli --version

$out = Join-Path $root "sgf-review-win64.zip"
if (Test-Path $out) { Remove-Item $out -Force }
Compress-Archive -Path (Join-Path $bundle "*") -DestinationPath $out
Write-Host "==> 完成: $out" -ForegroundColor Green
