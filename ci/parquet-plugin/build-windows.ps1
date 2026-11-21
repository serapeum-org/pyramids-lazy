# Windows driver compile for cibuildwheel `before-all`. The DLL analogue of build.sh.
#
# STATUS: first draft, CI-iterated, and the hardest platform. build.sh (ELF/gcc/patchelf) is
# proven on manylinux; this ports the five steps to Windows (MSVC, .dll, delvewheel does the
# final vendoring in cibuildwheel's repair). It has NOT run yet. Known rough edges the plan
# flagged: core's libgdal DLL has a content-hashed name that changes between releases, and there
# is a duckdb.dll dependency; both will surface here first. cpl_config.h comes from conda-forge
# win-64 libgdal-core via fetch_cpl_config.py (already tested on osx-arm64).
#
# Reads the same contract as build.sh via env: CORE_VERSION, PLAT (win_amd64), PYTAG, OUTPUT,
# ARROW_VERSION. Stages the plugin into src/pyramids_lazy/gdalplugins/ for the wheel build.
$ErrorActionPreference = "Stop"

$CoreVersion = $env:CORE_VERSION
$ArrowVersion = if ($env:ARROW_VERSION) { $env:ARROW_VERSION } else { "26.0.1" }
$Plat = if ($env:PLAT) { $env:PLAT } else { "win_amd64" }
$PyTag = if ($env:PYTAG) { $env:PYTAG } else { "cp312" }
$Work = if ($env:WORK) { $env:WORK } else { "C:/pp" }
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PkgPlugins = Join-Path (Resolve-Path (Join-Path $ScriptDir "../..")) "src/pyramids_lazy/gdalplugins"

New-Item -ItemType Directory -Force -Path $Work, $PkgPlugins | Out-Null
$py = "python"

Write-Host "=== 1. download the pyramids-core win_amd64 wheel ==="
Push-Location $Work
$spec = if ($CoreVersion) { "pyramids-core==$CoreVersion" } else { "pyramids-core" }
& $py -m pip download --no-deps --only-binary=:all: `
    --platform $Plat --python-version $PyTag.Substring(2) --implementation cp --abi $PyTag `
    -d "$Work/corewheel" $spec | Out-Null
if ($LASTEXITCODE -ne 0) { throw "could not download $spec for $Plat" }
Remove-Item -Recurse -Force "$Work/core" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$Work/core" | Out-Null
$wheel = Get-ChildItem "$Work/corewheel/pyramids_core-*.whl" | Select-Object -First 1
& $py -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" $wheel.FullName "$Work/core"
# Windows names it gdal-<hash>.dll (no lib prefix) under *.libs; the gdalplugins/*.dll are the
# per-driver plugins, not the main library. The hash changes between core releases.
$coreGdal = Get-ChildItem -Recurse "$Work/core" -Filter "gdal-*.dll" |
    Where-Object { $_.FullName -notmatch "gdalplugins" } | Select-Object -First 1
if (-not $coreGdal) { throw "no gdal-*.dll in the core win_amd64 wheel" }
Write-Host "vendored libgdal : $($coreGdal.Name)"
# GDAL_VERSION is passed in (PINNED_GDAL_VERSION): the Windows DLL name is gdal-<hash>.dll with
# NO version, so it cannot be derived like the Linux soname.
$gdalVersion = $env:GDAL_VERSION
if (-not $gdalVersion) { throw "GDAL_VERSION must be set (the win DLL name carries no version)" }

# Core's wheel ships only the DLL, no import .lib, so generate one from the DLL's exports to link
# the plugin against. dumpbin + lib come from MSVC; bring the VS dev environment onto PATH.
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vcTools = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
$vsPath = & $vswhere -latest -products * -requires $vcTools -property installationPath
Import-Module (Join-Path $vsPath "Common7\Tools\Microsoft.VisualStudio.DevShell.dll")
Enter-VsDevShell -VsInstallPath $vsPath -SkipAutomaticLocation -DevCmdArguments "-arch=x64 -host_arch=x64"
$gdalLib = "$Work/gdal.lib"
$def = "$Work/gdal.def"
# The LIBRARY line must name core's ACTUAL DLL (gdal-<hash>.dll). Without it, lib.exe derives the
# imported DLL name from /out ("gdal.dll"), so the plugin's import table would reference gdal.dll
# -- a name core does not ship (it vendors gdal-<hash>.dll) -- and the driver would fail to load
# at runtime with that module missing, which only a runtime verify catches. Naming it here makes
# the plugin import exactly the module core loads in-process.
Set-Content $def "LIBRARY $($coreGdal.Name)"
Add-Content $def "EXPORTS"
# dumpbin /exports lines look like: "    1    0 0000ABCD  SymbolName" -- take the 4th column.
& dumpbin /nologo /exports $coreGdal.FullName |
    Select-String -Pattern '^\s+\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+(\S+)' |
    ForEach-Object { $_.Matches[0].Groups[1].Value } | Add-Content $def
& lib /nologo "/def:$def" "/out:$gdalLib" /machine:x64
if ($LASTEXITCODE -ne 0) { throw "could not build an import lib from $($coreGdal.Name)" }
# Two header lines now precede the symbols (LIBRARY + EXPORTS).
Write-Host "generated import lib: $gdalLib -> $($coreGdal.Name) ($((Get-Content $def).Count - 2) symbols)"

Write-Host "=== 2. GDAL $gdalVersion sources + cpl_config.h from conda win-64 libgdal-core ==="
curl.exe -fsSL -o gdal.tar.gz "https://download.osgeo.org/gdal/$gdalVersion/gdal-$gdalVersion.tar.gz"
tar -xzf gdal.tar.gz
$gdalSrc = "$Work/gdal-$gdalVersion"
$genInclude = "$Work/gdal-include"
Remove-Item -Recurse -Force $genInclude -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $genInclude | Out-Null
$headerDirs = @("gcore", "gcore/multidim", "port", "ogr", "ogr/ogrsf_frmts",
    "ogr/ogrsf_frmts/generic", "frmts/mem", "alg", "apps")
foreach ($sub in $headerDirs) {
    Get-ChildItem "$gdalSrc/$sub/*.h" -ErrorAction SilentlyContinue | ForEach-Object {
        if (-not (Test-Path "$genInclude/$($_.Name)")) { Copy-Item $_.FullName $genInclude }
    }
}
Copy-Item "$gdalSrc/gcore/gdal_version.h.in" "$genInclude/gdal_version.h"
& $py -m pip install --quiet "zstandard==0.23.0"
& $py "$ScriptDir/fetch_cpl_config.py" $gdalVersion "win-64" $genInclude
if ($LASTEXITCODE -ne 0) { throw "could not obtain cpl_config.h for win-64" }

Write-Host "=== 3. minimal Arrow $ArrowVersion (MSVC) ==="
# Arrow 26 bundles boost, whose locale/CMakeLists.txt calls source_group(TREE) with a file
# (locale.hpp) beside the tree, not under it. CMake >= 3.28 (the runner's) makes that a hard
# error; 3.27.x tolerates it. Pin it and put it first on PATH for the Arrow + plugin builds.
& $py -m pip install --quiet "cmake==3.27.9"
if ($LASTEXITCODE -ne 0) { throw "could not pin cmake 3.27.9" }
$cmakeBin = & $py -c "import cmake, os; print(os.path.join(os.path.dirname(cmake.__file__), 'data', 'bin'))"
$env:PATH = "$cmakeBin;$env:PATH"
Write-Host "using cmake: $(& cmake --version | Select-Object -First 1)"
$arrowUrl = "https://downloads.apache.org/arrow/arrow-$ArrowVersion/apache-arrow-$ArrowVersion.tar.gz"
curl.exe -fsSL -o arrow.tar.gz $arrowUrl
tar -xzf arrow.tar.gz
$arrowSrc = "$Work/apache-arrow-$ArrowVersion/cpp"
$arrowPrefix = "$Work/arrow-prefix"
New-Item -ItemType Directory -Force -Path "$arrowSrc/build" | Out-Null
Push-Location "$arrowSrc/build"
cmake .. -DCMAKE_BUILD_TYPE=Release "-DCMAKE_INSTALL_PREFIX=$arrowPrefix" `
    -DARROW_DEPENDENCY_SOURCE=BUNDLED -DARROW_BUILD_SHARED=ON -DARROW_BUILD_STATIC=OFF `
    -DARROW_PARQUET=ON -DARROW_DATASET=ON -DARROW_COMPUTE=ON -DARROW_ACERO=ON `
    -DARROW_CSV=OFF -DARROW_JSON=OFF -DARROW_WITH_LZ4=ON -DARROW_WITH_SNAPPY=ON `
    -DARROW_WITH_ZLIB=ON -DARROW_WITH_ZSTD=ON
if ($LASTEXITCODE -ne 0) { throw "Arrow configure" }
cmake --build . --config Release
if ($LASTEXITCODE -ne 0) { throw "Arrow build" }
cmake --install . --config Release | Out-Null
Pop-Location

Write-Host "=== 4. ogr_Parquet as a standalone plugin against the wheel's libgdal ==="
$pluginBuild = "$Work/plugin-build"
New-Item -ItemType Directory -Force -Path $pluginBuild | Out-Null
Push-Location $pluginBuild
# GDAL_LIBRARY wants an import .lib; generating it from the DLL is the known CI-iteration step.
cmake "$gdalSrc/ogr/ogrsf_frmts/parquet" -DCMAKE_BUILD_TYPE=Release `
    "-DCMAKE_PREFIX_PATH=$arrowPrefix" "-DGDAL_INCLUDE_DIR=$genInclude" "-DGDAL_LIBRARY=$gdalLib" `
    "-DArrow_DIR=$arrowPrefix/lib/cmake/Arrow" "-DParquet_DIR=$arrowPrefix/lib/cmake/Parquet" `
    "-DArrowDataset_DIR=$arrowPrefix/lib/cmake/ArrowDataset" -DGDAL_USE_ARROWDATASET=ON
if ($LASTEXITCODE -ne 0) { throw "plugin configure" }
cmake --build . --config Release
if ($LASTEXITCODE -ne 0) { throw "plugin build" }
$pluginDll = Get-ChildItem -Recurse $pluginBuild -Filter "ogr_Parquet.dll" | Select-Object -First 1
if (-not $pluginDll) { throw "ogr_Parquet.dll was not produced" }
Pop-Location

Write-Host "=== 5. stage into the package; delvewheel (repair) vendors Arrow, excludes libgdal ==="
Copy-Item $pluginDll.FullName $PkgPlugins
Copy-Item "$gdalSrc/LICENSE.TXT" "$PkgPlugins/GDAL-LICENSE.txt"
Copy-Item "$Work/apache-arrow-$ArrowVersion/LICENSE.txt" "$PkgPlugins/Arrow-LICENSE.txt"
Copy-Item "$Work/apache-arrow-$ArrowVersion/NOTICE.txt" "$PkgPlugins/Arrow-NOTICE.txt"
# delvewheel --add-path (set in pyproject) points at the Arrow DLLs so repair can vendor them.
Copy-Item "$arrowPrefix/bin/*.dll" $PkgPlugins -ErrorAction SilentlyContinue
Write-Host "staged $($pluginDll.Name) into $PkgPlugins"
Pop-Location
