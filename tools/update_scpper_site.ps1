param(
    [switch]$PublishOnly,
    [switch]$FullSourceCrawl,
    [switch]$SkipLive,
    [switch]$SkipSourceCrawl,
    [switch]$SkipForum,
    [switch]$NoPause,
    [int]$Workers = 0,
    [int]$SourceWorkers = 0,
    [int]$ForumWorkers = 0,
    [int]$CommentsPerThread = 0
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8

$Workspace = "D:\downloads\lab4"
$SiteOut = Join-Path $Workspace "site-scpper"
$Repo = "gao-yi-fei/gao-yi-fei.github.io"
$Log = Join-Path ([Environment]::GetFolderPath("Desktop")) "piglin-me-update.log"

if ($Workers -le 0) {
    $Workers = [Math]::Max(24, [Math]::Min(48, [Environment]::ProcessorCount * 2))
}
if ($SourceWorkers -le 0) {
    $SourceWorkers = [Math]::Max(8, [Math]::Min(16, [Environment]::ProcessorCount))
}
if ($ForumWorkers -le 0) {
    $ForumWorkers = [Math]::Max(16, [Math]::Min(32, [Environment]::ProcessorCount * 2))
}

$BackupDir = Join-Path $Workspace ("backups\scp-wiki-mc-source-" + (Get-Date -Format "yyyyMMdd-HHmmss"))

function Write-Step($Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Pause-IfNeeded {
    if (-not $NoPause) {
        pause
    }
}

function Quote-ProcessArgument([string]$Value) {
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Invoke-Step {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $Workspace
    )

    Write-Step ("> {0} {1}" -f $FilePath, ($Arguments -join " "))
    $stdoutPath = Join-Path $env:TEMP ("piglin-step-{0}.out.log" -f ([guid]::NewGuid().ToString("N")))
    $stderrPath = Join-Path $env:TEMP ("piglin-step-{0}.err.log" -f ([guid]::NewGuid().ToString("N")))
    $argumentLine = ($Arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " "
    try {
        $process = Start-Process -FilePath $FilePath `
            -ArgumentList $argumentLine `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -NoNewWindow `
            -Wait `
            -PassThru

        $stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -LiteralPath $stdoutPath -Raw -Encoding UTF8 } else { "" }
        $stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw -Encoding UTF8 } else { "" }
        if ($stdout) {
            Write-Host $stdout
            Add-Content -Path $Log -Value $stdout -Encoding UTF8
        }
        if ($stderr) {
            Write-Host $stderr
            Add-Content -Path $Log -Value $stderr -Encoding UTF8
        }
        if ($process.ExitCode -ne 0) {
            $tail = ($stderr + "`n" + $stdout).Trim()
            if ($tail.Length -gt 1200) {
                $tail = $tail.Substring($tail.Length - 1200)
            }
            throw "$FilePath failed with exit code $($process.ExitCode). $tail"
        }
    } finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Get-LatestBackup {
    $backupRoot = Join-Path $Workspace "backups"
    $latestBackup = Get-ChildItem -LiteralPath $backupRoot -Directory -Filter "scp-wiki-mc-source-*" |
        Where-Object {
            -not (Test-Path -LiteralPath (Join-Path $_.FullName ".crawl-in-progress")) -and
            (
                (Test-Path -LiteralPath (Join-Path $_.FullName "index.csv")) -or
                (Test-Path -LiteralPath (Join-Path $_.FullName "manifest.csv"))
            )
        } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $latestBackup) {
        throw "No valid source backup with index.csv or manifest.csv found in $backupRoot. Run with -FullSourceCrawl once."
    }
    return $latestBackup.FullName
}

function Copy-BackupForIncrementalCrawl {
    param(
        [string]$SourceBackup,
        [string]$DestinationBackup
    )

    New-Item -ItemType Directory -Force -Path $DestinationBackup | Out-Null
    Set-Content -LiteralPath (Join-Path $DestinationBackup ".crawl-in-progress") -Value (Get-Date -Format "o") -Encoding UTF8
    Get-ChildItem -LiteralPath $SourceBackup -Force |
        Where-Object { $_.Name -ne "_previous" } |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $DestinationBackup -Recurse -Force
        }

    $previousDir = Join-Path $DestinationBackup "_previous"
    New-Item -ItemType Directory -Force -Path $previousDir | Out-Null
    foreach ($name in @(
        "index.csv",
        "manifest.csv",
        "index.jsonl",
        "failed.jsonl",
        "discovered_urls.txt",
        "archived_deleted.csv",
        "README.md"
    )) {
        $sourcePath = Join-Path $SourceBackup $name
        if (Test-Path -LiteralPath $sourcePath) {
            Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $previousDir $name) -Force
        }
    }

    foreach ($staleName in @("manifest.csv", "index.jsonl")) {
        $stalePath = Join-Path $DestinationBackup $staleName
        if (Test-Path -LiteralPath $stalePath) {
            Remove-Item -LiteralPath $stalePath -Force
        }
    }
}

function Complete-SourceBackup {
    param([string]$CompletedBackup)

    $markerPath = Join-Path $CompletedBackup ".crawl-in-progress"
    if (Test-Path -LiteralPath $markerPath) {
        Remove-Item -LiteralPath $markerPath -Force
    }
    Set-Content -LiteralPath (Join-Path $CompletedBackup ".crawl-complete") -Value (Get-Date -Format "o") -Encoding UTF8
}

function Crawl-Source {
    if ($PublishOnly) {
        return
    }
    if ($SkipSourceCrawl) {
        $script:BackupDir = Get-LatestBackup
        Write-Step "Source crawl explicitly skipped; using latest valid backup $script:BackupDir."
        return
    }
    if (-not $FullSourceCrawl) {
        $previousBackup = Get-LatestBackup
        Write-Step "Preparing incremental source backup from $previousBackup to $BackupDir."
        Copy-BackupForIncrementalCrawl -SourceBackup $previousBackup -DestinationBackup $BackupDir
        Write-Step "Discovering current Wikidot pages; reusing existing source files and fetching new or missing pages with workers=$SourceWorkers."
        Invoke-Step "python" @(
            "tools\crawl_wikidot_sources.py",
            "--out", $BackupDir,
            "--workers", [string]$SourceWorkers,
            "--delay", "0.05",
            "--timeout", "45",
            "--retries", "4",
            "--save-raw",
            "--with-metadata",
            "--reuse-existing",
            "--allow-failures"
        )
        Complete-SourceBackup -CompletedBackup $BackupDir
        return
    }

    Write-Step "Crawling fresh Wikidot source backup to $BackupDir with workers=$SourceWorkers."
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    Set-Content -LiteralPath (Join-Path $BackupDir ".crawl-in-progress") -Value (Get-Date -Format "o") -Encoding UTF8
    Invoke-Step "python" @(
        "tools\crawl_wikidot_sources.py",
        "--out", $BackupDir,
        "--workers", [string]$SourceWorkers,
        "--delay", "0.05",
        "--timeout", "45",
        "--retries", "4",
        "--save-raw",
        "--with-metadata",
        "--allow-failures"
    )
    Complete-SourceBackup -CompletedBackup $BackupDir
}

function Archive-CurrentSiteData {
    if ($PublishOnly) {
        return
    }

    $dataDir = Join-Path $SiteOut "data"
    $searchIndex = Join-Path $dataDir "search-index.json.gz"
    if (-not (Test-Path -LiteralPath $searchIndex)) {
        Write-Step "No previous site data found; skipping data snapshot."
        return
    }

    $snapshotDir = Join-Path $SiteOut "downloads\snapshots"
    New-Item -ItemType Directory -Force -Path $snapshotDir | Out-Null
    $zipPath = Join-Path $snapshotDir ("scpper-mc-site-data-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".zip")
    $snapshotItems = @((Join-Path $dataDir "*"))
    foreach ($name in @("index.html", "users.html", "forum.html")) {
        $path = Join-Path $SiteOut $name
        if (Test-Path -LiteralPath $path) {
            $snapshotItems += $path
        }
    }

    Write-Step "Archiving current site data snapshot to $zipPath."
    Compress-Archive -Path $snapshotItems -DestinationPath $zipPath -CompressionLevel Optimal -Force
}

function Build-Site {
    if ($PublishOnly) {
        Write-Step "Skipping rebuild; publishing current site-scpper output."
        return
    }

    $args = @(
        "tools\build_scpper_lite.py",
        "--backup", $BackupDir,
        "--workers", [string]$Workers,
        "--forum-workers", [string]$ForumWorkers,
        "--timeout", "45",
        "--retries", "3",
        "--comments-per-thread", [string]$CommentsPerThread,
        "--out", "site-scpper"
    )
    if ($SkipLive) {
        $args += "--skip-live"
    }
    if ($SkipForum) {
        $args += "--skip-forum"
    }

    Write-Step "Using workers=$Workers; forum-workers=$ForumWorkers; comments-per-thread=$CommentsPerThread."
    Invoke-Step "python" $args
}

function Repair-RequiredFields {
    if ($PublishOnly) {
        return
    }

    Write-Step "Auditing and repairing required author, rating, voter, comment, and source fields."
    Invoke-Step "python" @(
        "tools\repair_required_fields.py",
        "--site", "site-scpper",
        "--backup", $BackupDir,
        "--workers", [string][Math]::Max(12, [Math]::Min($Workers, 32)),
        "--timeout", "60",
        "--retries", "10",
        "--source-retries", "8",
        "--comments-per-thread", [string]$CommentsPerThread,
        "--soft-comment-failures"
    )
}

function Package-Backup {
    if ($PublishOnly) {
        return
    }

    $downloadDir = Join-Path $SiteOut "downloads"
    New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

    $backupLeaf = Split-Path -Leaf $BackupDir
    $zipPath = Join-Path $downloadDir "$backupLeaf.zip"

    if (Test-Path -LiteralPath $zipPath) {
        Write-Step "Backup zip already exists; skipping compression: $zipPath."
    } else {
        Write-Step "Packaging source backup to $zipPath."
        Compress-Archive -Path (Join-Path $BackupDir "*") -DestinationPath $zipPath -CompressionLevel Optimal -Force
    }

    $indexPath = Join-Path $SiteOut "index.html"
    $newHref = "/downloads/$backupLeaf.zip"
    $html = Get-Content -LiteralPath $indexPath -Raw -Encoding UTF8
    $html = [regex]::Replace(
        $html,
        'href="/downloads/scp-wiki-mc-source-[^"]+\.zip"',
        'href="' + $newHref + '"'
    )
    Set-Content -LiteralPath $indexPath -Value $html -Encoding UTF8
    Write-Step "Updated backup link to $newHref."
}

function Publish-Site {
    Invoke-Step "python" @(
        "tools\publish_github_pages_fast.py",
        "--site", "site-scpper",
        "--repo", $Repo,
        "--branch", "main",
        "--message", "Refresh SCPPER-MC site data"
    )
    Write-Step "Published to https://piglin.me/"
}

try {
    Set-Location $Workspace
    "" | Set-Content -Path $Log -Encoding UTF8
    Write-Step "Starting piglin.me SCPPER-MC update."
    Crawl-Source
    Archive-CurrentSiteData
    Build-Site
    Repair-RequiredFields
    Package-Backup
    Publish-Site
    Write-Step "Done."
} catch {
    Write-Step "FAILED: $($_.Exception.Message)"
    Write-Host ""
    Write-Host "Update failed. Log: $Log"
    Pause-IfNeeded
    exit 1
}

Write-Host ""
Write-Host "Update complete. Log: $Log"
Pause-IfNeeded
