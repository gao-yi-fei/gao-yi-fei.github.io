param(
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8

if ([System.Threading.Thread]::CurrentThread.ApartmentState -ne "STA") {
    throw "This GUI must be started with powershell -STA."
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$Workspace = "D:\downloads\lab4"
$UpdateScript = Join-Path $Workspace "tools\update_scpper_site.ps1"
$SiteOut = Join-Path $Workspace "site-scpper"
$BackupRoot = Join-Path $Workspace "backups"
$Desktop = "D:\Desktop"
$Log = Join-Path $Desktop "piglin-me-update.log"

$Colors = @{
    Page = [System.Drawing.ColorTranslator]::FromHtml("#0F1418")
    Card = [System.Drawing.ColorTranslator]::FromHtml("#172026")
    Panel = [System.Drawing.ColorTranslator]::FromHtml("#111A20")
    Line = [System.Drawing.ColorTranslator]::FromHtml("#2A3942")
    Text = [System.Drawing.ColorTranslator]::FromHtml("#E6EDF0")
    Subtle = [System.Drawing.ColorTranslator]::FromHtml("#97A6AD")
    Accent = [System.Drawing.ColorTranslator]::FromHtml("#28C7A0")
    AccentDark = [System.Drawing.ColorTranslator]::FromHtml("#0D2B26")
    Warn = [System.Drawing.ColorTranslator]::FromHtml("#F2B84B")
    WarnDark = [System.Drawing.ColorTranslator]::FromHtml("#302719")
    Danger = [System.Drawing.ColorTranslator]::FromHtml("#E85D5D")
    DangerDark = [System.Drawing.ColorTranslator]::FromHtml("#351B1E")
    Input = [System.Drawing.ColorTranslator]::FromHtml("#0B1318")
    Button = [System.Drawing.ColorTranslator]::FromHtml("#223039")
}

function New-Font([float]$Size, [System.Drawing.FontStyle]$Style = [System.Drawing.FontStyle]::Regular) {
    return New-Object System.Drawing.Font("Microsoft YaHei UI", $Size, $Style)
}

function Set-ControlColors($Control, $Back, $Fore) {
    $Control.BackColor = $Back
    $Control.ForeColor = $Fore
}

function New-Label([string]$Text, [float]$Size = 9, [System.Drawing.FontStyle]$Style = [System.Drawing.FontStyle]::Regular, $Color = $Colors.Text) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.AutoSize = $false
    $label.ForeColor = $Color
    $label.Font = New-Font $Size $Style
    $label.Margin = New-Object System.Windows.Forms.Padding(0)
    return $label
}

function New-Card([int]$Height) {
    $panel = New-Object System.Windows.Forms.Panel
    $panel.Width = 320
    $panel.Height = $Height
    $panel.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 12)
    $panel.Padding = New-Object System.Windows.Forms.Padding(14)
    $panel.BackColor = $Colors.Card
    return $panel
}

function New-Button([string]$Text, $BackColor = $Colors.Button, $ForeColor = $Colors.Text) {
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $Text
    $button.Height = 38
    $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $button.FlatAppearance.BorderColor = $Colors.Line
    $button.FlatAppearance.MouseOverBackColor = [System.Drawing.ColorTranslator]::FromHtml("#2D3D47")
    $button.FlatAppearance.MouseDownBackColor = [System.Drawing.ColorTranslator]::FromHtml("#1C2931")
    $button.BackColor = $BackColor
    $button.ForeColor = $ForeColor
    $button.Font = New-Font 9.5 ([System.Drawing.FontStyle]::Bold)
    $button.Cursor = [System.Windows.Forms.Cursors]::Hand
    return $button
}

function New-TextBox([string]$Text = "") {
    $box = New-Object System.Windows.Forms.TextBox
    $box.Text = $Text
    $box.Height = 28
    $box.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $box.BackColor = $Colors.Input
    $box.ForeColor = $Colors.Text
    $box.Font = New-Font 9
    return $box
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "SCPPER-MC 更新面板"
$form.Size = New-Object System.Drawing.Size(1080, 720)
$form.MinimumSize = New-Object System.Drawing.Size(940, 640)
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.BackColor = $Colors.Page
$form.ForeColor = $Colors.Text
$form.Font = New-Font 9
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Dpi

$root = New-Object System.Windows.Forms.TableLayoutPanel
$root.Dock = [System.Windows.Forms.DockStyle]::Fill
$root.Padding = New-Object System.Windows.Forms.Padding(22)
$root.BackColor = $Colors.Page
$root.RowCount = 2
$root.ColumnCount = 1
[void]$root.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 84)))
[void]$root.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
$form.Controls.Add($root)

$header = New-Object System.Windows.Forms.Panel
$header.Dock = [System.Windows.Forms.DockStyle]::Fill
$header.BackColor = $Colors.Page
$root.Controls.Add($header, 0, 0)

$title = New-Label "SCPPER-MC" 29 ([System.Drawing.FontStyle]::Bold)
$title.Location = New-Object System.Drawing.Point(0, 2)
$title.Size = New-Object System.Drawing.Size(520, 42)
$header.Controls.Add($title)

$subtitle = New-Label "piglin.me 自动更新与发布面板 · created by piglin" 9 ([System.Drawing.FontStyle]::Regular) $Colors.Subtle
$subtitle.Location = New-Object System.Drawing.Point(2, 48)
$subtitle.Size = New-Object System.Drawing.Size(560, 22)
$header.Controls.Add($subtitle)

$statusPanel = New-Object System.Windows.Forms.Panel
$statusPanel.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$statusPanel.Location = New-Object System.Drawing.Point(860, 8)
$statusPanel.Size = New-Object System.Drawing.Size(156, 38)
$statusPanel.BackColor = $Colors.AccentDark
$statusPanel.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$header.Controls.Add($statusPanel)

$statusText = New-Label "待命" 9.5 ([System.Drawing.FontStyle]::Bold) $Colors.Accent
$statusText.Dock = [System.Windows.Forms.DockStyle]::Fill
$statusText.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
$statusPanel.Controls.Add($statusText)

$main = New-Object System.Windows.Forms.TableLayoutPanel
$main.Dock = [System.Windows.Forms.DockStyle]::Fill
$main.BackColor = $Colors.Page
$main.ColumnCount = 3
$main.RowCount = 1
[void]$main.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 350)))
[void]$main.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 18)))
[void]$main.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
$root.Controls.Add($main, 0, 1)

$left = New-Object System.Windows.Forms.FlowLayoutPanel
$left.Dock = [System.Windows.Forms.DockStyle]::Fill
$left.FlowDirection = [System.Windows.Forms.FlowDirection]::TopDown
$left.WrapContents = $false
$left.AutoScroll = $true
$left.BackColor = $Colors.Page
$main.Controls.Add($left, 0, 0)

$modeCard = New-Card 128
$modeTitle = New-Label "更新模式" 12 ([System.Drawing.FontStyle]::Bold)
$modeTitle.SetBounds(14, 14, 280, 24)
$modeCard.Controls.Add($modeTitle)
$modeHelp = New-Label "默认会发现新增页面、保留删除页存档，并重建后发布。" 8.5 ([System.Drawing.FontStyle]::Regular) $Colors.Subtle
$modeHelp.SetBounds(14, 42, 292, 34)
$modeCard.Controls.Add($modeHelp)
$modeBox = New-Object System.Windows.Forms.ComboBox
$modeBox.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
$modeBox.SetBounds(14, 82, 292, 30)
$modeBox.BackColor = $Colors.Input
$modeBox.ForeColor = $Colors.Text
$modeBox.Font = New-Font 9
[void]$modeBox.Items.Add("智能增量更新（推荐）")
[void]$modeBox.Items.Add("完整源码重爬")
[void]$modeBox.Items.Add("仅发布当前站点")
[void]$modeBox.Items.Add("跳过源码发现，重建并发布")
$modeBox.SelectedIndex = 0
$modeCard.Controls.Add($modeBox)
$left.Controls.Add($modeCard)

$skipCard = New-Card 112
$skipTitle = New-Label "可选跳过项" 12 ([System.Drawing.FontStyle]::Bold)
$skipTitle.SetBounds(14, 14, 280, 24)
$skipCard.Controls.Add($skipTitle)
$skipLiveBox = New-Object System.Windows.Forms.CheckBox
$skipLiveBox.Text = "跳过 live 页面数据"
$skipLiveBox.SetBounds(14, 48, 240, 24)
Set-ControlColors $skipLiveBox $Colors.Card $Colors.Text
$skipCard.Controls.Add($skipLiveBox)
$skipForumBox = New-Object System.Windows.Forms.CheckBox
$skipForumBox.Text = "跳过讨论区"
$skipForumBox.SetBounds(14, 75, 240, 24)
Set-ControlColors $skipForumBox $Colors.Card $Colors.Text
$skipCard.Controls.Add($skipForumBox)
$left.Controls.Add($skipCard)

$perfCard = New-Card 186
$perfTitle = New-Label "性能参数" 12 ([System.Drawing.FontStyle]::Bold)
$perfTitle.SetBounds(14, 14, 280, 24)
$perfCard.Controls.Add($perfTitle)
$perfHelp = New-Label "留空或填 0 表示自动。Wikidot 抽风时别拉太满。" 8.5 ([System.Drawing.FontStyle]::Regular) $Colors.Subtle
$perfHelp.SetBounds(14, 42, 292, 34)
$perfCard.Controls.Add($perfHelp)

$workersLabel = New-Label "页面并发" 8.5
$workersLabel.SetBounds(14, 82, 132, 18)
$perfCard.Controls.Add($workersLabel)
$sourceLabel = New-Label "源码并发" 8.5
$sourceLabel.SetBounds(174, 82, 132, 18)
$perfCard.Controls.Add($sourceLabel)
$workersBox = New-TextBox "0"
$workersBox.SetBounds(14, 104, 132, 28)
$perfCard.Controls.Add($workersBox)
$sourceWorkersBox = New-TextBox "0"
$sourceWorkersBox.SetBounds(174, 104, 132, 28)
$perfCard.Controls.Add($sourceWorkersBox)
$forumLabel = New-Label "论坛并发" 8.5
$forumLabel.SetBounds(14, 140, 132, 18)
$perfCard.Controls.Add($forumLabel)
$commentsLabel = New-Label "每串帖子上限" 8.5
$commentsLabel.SetBounds(174, 140, 132, 18)
$perfCard.Controls.Add($commentsLabel)
$forumWorkersBox = New-TextBox "0"
$forumWorkersBox.SetBounds(14, 160, 132, 28)
$perfCard.Controls.Add($forumWorkersBox)
$commentsBox = New-TextBox "0"
$commentsBox.SetBounds(174, 160, 132, 28)
$perfCard.Controls.Add($commentsBox)
$left.Controls.Add($perfCard)

$envCard = New-Card 124
$envTitle = New-Label "当前环境" 12 ([System.Drawing.FontStyle]::Bold)
$envTitle.SetBounds(14, 14, 280, 24)
$envCard.Controls.Add($envTitle)
$lastRunText = New-Label "" 8.5 ([System.Drawing.FontStyle]::Regular) $Colors.Subtle
$lastRunText.SetBounds(14, 46, 292, 22)
$envCard.Controls.Add($lastRunText)
$backupText = New-Label "" 8.5 ([System.Drawing.FontStyle]::Regular) $Colors.Subtle
$backupText.SetBounds(14, 68, 292, 22)
$envCard.Controls.Add($backupText)
$logPathText = New-Label "日志：D:\Desktop\piglin-me-update.log" 8.5 ([System.Drawing.FontStyle]::Regular) $Colors.Subtle
$logPathText.SetBounds(14, 90, 292, 22)
$envCard.Controls.Add($logPathText)
$left.Controls.Add($envCard)

$startStopPanel = New-Object System.Windows.Forms.TableLayoutPanel
$startStopPanel.Width = 320
$startStopPanel.Height = 44
$startStopPanel.Margin = New-Object System.Windows.Forms.Padding(0, 2, 0, 12)
$startStopPanel.ColumnCount = 2
$startStopPanel.RowCount = 1
[void]$startStopPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50)))
[void]$startStopPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50)))
$startButton = New-Button "开始更新" $Colors.Accent ([System.Drawing.ColorTranslator]::FromHtml("#06231D"))
$startButton.Dock = [System.Windows.Forms.DockStyle]::Fill
$startButton.Margin = New-Object System.Windows.Forms.Padding(0, 0, 6, 0)
$stopButton = New-Button "停止" $Colors.DangerDark $Colors.Text
$stopButton.Dock = [System.Windows.Forms.DockStyle]::Fill
$stopButton.Margin = New-Object System.Windows.Forms.Padding(6, 0, 0, 0)
$stopButton.Enabled = $false
$startStopPanel.Controls.Add($startButton, 0, 0)
$startStopPanel.Controls.Add($stopButton, 1, 0)
$left.Controls.Add($startStopPanel)

$quickPanel = New-Object System.Windows.Forms.TableLayoutPanel
$quickPanel.Width = 320
$quickPanel.Height = 88
$quickPanel.Margin = New-Object System.Windows.Forms.Padding(0)
$quickPanel.ColumnCount = 2
$quickPanel.RowCount = 2
[void]$quickPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50)))
[void]$quickPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50)))
[void]$quickPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 50)))
[void]$quickPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 50)))
$openSiteButton = New-Button "打开网站"
$openLogButton = New-Button "打开日志"
$openFolderButton = New-Button "打开项目"
$openBackupsButton = New-Button "打开备份"
foreach ($button in @($openSiteButton, $openLogButton, $openFolderButton, $openBackupsButton)) {
    $button.Dock = [System.Windows.Forms.DockStyle]::Fill
    $button.Margin = New-Object System.Windows.Forms.Padding(0, 0, 8, 8)
}
$openLogButton.Margin = New-Object System.Windows.Forms.Padding(8, 0, 0, 8)
$openBackupsButton.Margin = New-Object System.Windows.Forms.Padding(8, 0, 0, 8)
$quickPanel.Controls.Add($openSiteButton, 0, 0)
$quickPanel.Controls.Add($openLogButton, 1, 0)
$quickPanel.Controls.Add($openFolderButton, 0, 1)
$quickPanel.Controls.Add($openBackupsButton, 1, 1)
$left.Controls.Add($quickPanel)

$right = New-Object System.Windows.Forms.Panel
$right.Dock = [System.Windows.Forms.DockStyle]::Fill
$right.Padding = New-Object System.Windows.Forms.Padding(16)
$right.BackColor = $Colors.Card
$main.Controls.Add($right, 2, 0)

$logTitle = New-Label "实时日志" 12 ([System.Drawing.FontStyle]::Bold)
$logTitle.SetBounds(16, 14, 220, 24)
$right.Controls.Add($logTitle)
$logHelp = New-Label "运行时会自动滚动；失败时先看最后几行。" 8.5 ([System.Drawing.FontStyle]::Regular) $Colors.Subtle
$logHelp.SetBounds(16, 42, 380, 22)
$right.Controls.Add($logHelp)
$clearLogButton = New-Button "清空显示"
$clearLogButton.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$clearLogButton.SetBounds(520, 16, 102, 32)
$right.Controls.Add($clearLogButton)

$commandPreview = New-TextBox
$commandPreview.ReadOnly = $true
$commandPreview.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Left -bor [System.Windows.Forms.AnchorStyles]::Right
$commandPreview.SetBounds(16, 78, 606, 30)
$commandPreview.ForeColor = [System.Drawing.ColorTranslator]::FromHtml("#BFD9D2")
$right.Controls.Add($commandPreview)

$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Left -bor [System.Windows.Forms.AnchorStyles]::Right
$progress.SetBounds(16, 122, 606, 8)
$progress.Style = [System.Windows.Forms.ProgressBarStyle]::Continuous
$right.Controls.Add($progress)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.WordWrap = $false
$logBox.ScrollBars = [System.Windows.Forms.ScrollBars]::Both
$logBox.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$logBox.BackColor = [System.Drawing.ColorTranslator]::FromHtml("#081014")
$logBox.ForeColor = [System.Drawing.ColorTranslator]::FromHtml("#DCE7EA")
$logBox.Font = New-Object System.Drawing.Font("Consolas", 9)
$logBox.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Bottom -bor [System.Windows.Forms.AnchorStyles]::Left -bor [System.Windows.Forms.AnchorStyles]::Right
$logBox.SetBounds(16, 146, 606, 470)
$right.Controls.Add($logBox)

$right.Add_SizeChanged({
    $clearLogButton.Left = $right.ClientSize.Width - $clearLogButton.Width - 16
    $commandPreview.Width = $right.ClientSize.Width - 32
    $progress.Width = $right.ClientSize.Width - 32
    $logBox.Width = $right.ClientSize.Width - 32
    $logBox.Height = $right.ClientSize.Height - $logBox.Top - 16
})

$left.Add_SizeChanged({
    foreach ($control in $left.Controls) {
        $control.Width = [Math]::Max(260, $left.ClientSize.Width - 22)
    }
})

$script:Process = $null
$script:LastLogLength = 0

function Set-Status([string]$Text, $ForeColor, $BackColor) {
    $statusText.Text = $Text
    $statusText.ForeColor = $ForeColor
    $statusPanel.BackColor = $BackColor
}

function Quote-Argument([string]$Value) {
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Join-CommandLine([string[]]$Arguments) {
    return ($Arguments | ForEach-Object { Quote-Argument $_ }) -join " "
}

function Add-IntegerArgument([string[]]$Arguments, [string]$Name, [System.Windows.Forms.TextBox]$TextBox) {
    $value = $TextBox.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($value) -or $value -eq "0") {
        return $Arguments
    }
    if ($value -notmatch '^\d+$') {
        throw "$Name 必须是非负整数。"
    }
    return $Arguments + @("-$Name", $value)
}

function Get-UpdateArguments {
    $arguments = @("-NoPause")
    switch ($modeBox.SelectedIndex) {
        1 { $arguments += "-FullSourceCrawl" }
        2 { $arguments += "-PublishOnly" }
        3 { $arguments += "-SkipSourceCrawl" }
    }
    if ($skipLiveBox.Checked) {
        $arguments += "-SkipLive"
    }
    if ($skipForumBox.Checked) {
        $arguments += "-SkipForum"
    }
    $arguments = Add-IntegerArgument $arguments "Workers" $workersBox
    $arguments = Add-IntegerArgument $arguments "SourceWorkers" $sourceWorkersBox
    $arguments = Add-IntegerArgument $arguments "ForumWorkers" $forumWorkersBox
    $arguments = Add-IntegerArgument $arguments "CommentsPerThread" $commentsBox
    return $arguments
}

function Update-Preview {
    try {
        $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $UpdateScript) + (Get-UpdateArguments)
        $commandPreview.Text = "powershell " + (Join-CommandLine $arguments)
    } catch {
        $commandPreview.Text = $_.Exception.Message
    }
}

function Append-LogText([string]$Text) {
    if ([string]::IsNullOrEmpty($Text)) {
        return
    }
    $logBox.AppendText($Text)
    if (-not $Text.EndsWith("`n")) {
        $logBox.AppendText("`r`n")
    }
    $logBox.SelectionStart = $logBox.TextLength
    $logBox.ScrollToCaret()
}

function Read-NewLogText {
    if (-not (Test-Path -LiteralPath $Log)) {
        return
    }
    $stream = $null
    $reader = $null
    try {
        $stream = [System.IO.File]::Open($Log, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        if ($stream.Length -lt $script:LastLogLength) {
            $script:LastLogLength = 0
            $logBox.Clear()
        }
        [void]$stream.Seek($script:LastLogLength, [System.IO.SeekOrigin]::Begin)
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8, $true)
        $text = $reader.ReadToEnd()
        $script:LastLogLength = $stream.Position
        Append-LogText $text
    } finally {
        if ($reader) {
            $reader.Close()
        } elseif ($stream) {
            $stream.Close()
        }
    }
}

function Refresh-EnvironmentText {
    if (Test-Path -LiteralPath $Log) {
        $item = Get-Item -LiteralPath $Log
        $lastRunText.Text = "最近日志：" + $item.LastWriteTime.ToString("yyyy/MM/dd HH:mm:ss")
    } else {
        $lastRunText.Text = "最近日志：暂无"
    }

    $latestBackup = $null
    if (Test-Path -LiteralPath $BackupRoot) {
        $latestBackup = Get-ChildItem -LiteralPath $BackupRoot -Directory -Filter "scp-wiki-mc-source-*" |
            Where-Object {
                -not (Test-Path -LiteralPath (Join-Path $_.FullName ".crawl-in-progress")) -and
                (
                    (Test-Path -LiteralPath (Join-Path $_.FullName "index.csv")) -or
                    (Test-Path -LiteralPath (Join-Path $_.FullName "manifest.csv"))
                )
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
    }
    if ($latestBackup) {
        $backupText.Text = "最近备份：" + $latestBackup.Name
    } else {
        $backupText.Text = "最近备份：未找到有效备份"
    }
}

function Set-RunningState([bool]$Running) {
    $startButton.Enabled = -not $Running
    $stopButton.Enabled = $Running
    $modeBox.Enabled = -not $Running
    $skipLiveBox.Enabled = -not $Running
    $skipForumBox.Enabled = -not $Running
    $workersBox.Enabled = -not $Running
    $sourceWorkersBox.Enabled = -not $Running
    $forumWorkersBox.Enabled = -not $Running
    $commentsBox.Enabled = -not $Running
    $progress.Style = if ($Running) { [System.Windows.Forms.ProgressBarStyle]::Marquee } else { [System.Windows.Forms.ProgressBarStyle]::Continuous }
}

function Stop-ProcessTree([int]$ProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 700
$timer.Add_Tick({
    Read-NewLogText
    if ($script:Process -and $script:Process.HasExited) {
        Read-NewLogText
        $exitCode = $script:Process.ExitCode
        $script:Process.Dispose()
        $script:Process = $null
        $timer.Stop()
        Set-RunningState $false
        Refresh-EnvironmentText
        if ($exitCode -eq 0) {
            Set-Status "完成" $Colors.Accent $Colors.AccentDark
            Append-LogText "`r`n完成：已按当前模式结束。"
        } else {
            Set-Status "失败：退出码 $exitCode" $Colors.Danger $Colors.DangerDark
            Append-LogText "`r`n失败：退出码 $exitCode。请查看日志末尾。"
        }
    }
})

$startButton.Add_Click({
    try {
        if (-not (Test-Path -LiteralPath $UpdateScript)) {
            throw "找不到更新脚本：$UpdateScript"
        }
        $runArguments = Get-UpdateArguments
        $allArguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $UpdateScript) + $runArguments

        $logBox.Clear()
        $script:LastLogLength = 0
        Append-LogText ("启动：" + "powershell " + (Join-CommandLine $allArguments))
        Append-LogText ""

        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = "powershell.exe"
        $startInfo.Arguments = Join-CommandLine $allArguments
        $startInfo.WorkingDirectory = $Workspace
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        [void]$process.Start()
        $script:Process = $process

        Set-Status "运行中" $Colors.Warn $Colors.WarnDark
        Set-RunningState $true
        $timer.Start()
    } catch {
        Set-Status "启动失败" $Colors.Danger $Colors.DangerDark
        Append-LogText ("启动失败：" + $_.Exception.Message)
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "启动失败", "OK", "Error") | Out-Null
    }
})

$stopButton.Add_Click({
    if ($script:Process -and -not $script:Process.HasExited) {
        Append-LogText "`r`n正在停止进程树..."
        Stop-ProcessTree -ProcessId $script:Process.Id
        Set-Status "已请求停止" $Colors.Danger $Colors.DangerDark
    }
})

$openSiteButton.Add_Click({ Start-Process "https://piglin.me/" })
$openLogButton.Add_Click({
    if (-not (Test-Path -LiteralPath $Log)) {
        New-Item -ItemType File -Force -Path $Log | Out-Null
    }
    Start-Process "notepad.exe" -ArgumentList @($Log)
})
$openFolderButton.Add_Click({ Start-Process "explorer.exe" -ArgumentList @($Workspace) })
$openBackupsButton.Add_Click({
    New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
    Start-Process "explorer.exe" -ArgumentList @($BackupRoot)
})
$clearLogButton.Add_Click({ $logBox.Clear() })

$modeBox.Add_SelectedIndexChanged({ Update-Preview })
$skipLiveBox.Add_CheckedChanged({ Update-Preview })
$skipForumBox.Add_CheckedChanged({ Update-Preview })
foreach ($box in @($workersBox, $sourceWorkersBox, $forumWorkersBox, $commentsBox)) {
    $box.Add_TextChanged({ Update-Preview })
}

$form.Add_FormClosing({
    if ($script:Process -and -not $script:Process.HasExited) {
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "更新仍在运行。要停止它并关闭面板吗？",
            "确认关闭",
            "YesNo",
            "Warning"
        )
        if ($answer -eq "Yes") {
            Stop-ProcessTree -ProcessId $script:Process.Id
        } else {
            $_.Cancel = $true
        }
    }
})

Refresh-EnvironmentText
Update-Preview
if (Test-Path -LiteralPath $Log) {
    Read-NewLogText
}

if ($SelfTest) {
    "GUI self-test ok"
    return
}

[System.Windows.Forms.Application]::Run($form)
