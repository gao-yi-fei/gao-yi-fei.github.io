param(
  [string]$Path,
  [string]$Text,
  [switch]$NoSelectAll
)

$ErrorActionPreference = "Stop"

if ($Path) {
  $payload = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
} elseif ($PSBoundParameters.ContainsKey("Text")) {
  $payload = $Text
} else {
  throw "Provide -Path or -Text."
}

$clipboardSet = $false
for ($i = 0; $i -lt 8; $i++) {
  try {
    Set-Clipboard -Value $payload
    $clipboardSet = $true
    break
  } catch {
    Start-Sleep -Milliseconds 150
  }
}

if (-not $clipboardSet) {
  throw "Could not set clipboard after retrying."
}

if (-not ("WinFocus" -as [type])) {
  Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinFocus {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
  [DllImport("user32.dll")] public static extern void SwitchToThisWindow(IntPtr hWnd, bool fAltTab);
}
"@
}

$focusBlockers = Get-Process -ErrorAction SilentlyContinue |
  Where-Object { $_.MainWindowHandle -ne 0 -and ($_.MainWindowTitle -like "*MuMu*" -or $_.MainWindowTitle -like "*MAA*") }

foreach ($blocker in $focusBlockers) {
  [WinFocus]::ShowWindowAsync($blocker.MainWindowHandle, 6) | Out-Null
}

$target = Get-Process msedge -ErrorAction SilentlyContinue |
  Where-Object { $_.MainWindowHandle -ne 0 -and ($_.MainWindowTitle -like "*codex-scp-test*" -or $_.MainWindowTitle -like "*SCP-CD*") } |
  Select-Object -First 1

if (-not $target) {
  $target = Get-Process msedge -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
}

if ($target) {
  $topMost = [IntPtr]::new(-1)
  $notTopMost = [IntPtr]::new(-2)
  $noMoveNoSize = 0x0001 -bor 0x0002 -bor 0x0040
  [WinFocus]::ShowWindowAsync($target.MainWindowHandle, 9) | Out-Null
  Start-Sleep -Milliseconds 250
  [WinFocus]::SetWindowPos($target.MainWindowHandle, $topMost, 0, 0, 0, 0, $noMoveNoSize) | Out-Null
  Start-Sleep -Milliseconds 80
  [WinFocus]::SetWindowPos($target.MainWindowHandle, $notTopMost, 0, 0, 0, 0, $noMoveNoSize) | Out-Null
  for ($i = 0; $i -lt 10; $i++) {
    [WinFocus]::SetForegroundWindow($target.MainWindowHandle) | Out-Null
    [WinFocus]::SwitchToThisWindow($target.MainWindowHandle, $true)
    Start-Sleep -Milliseconds 150
    if ([WinFocus]::GetForegroundWindow() -eq $target.MainWindowHandle) {
      break
    }
  }
  Start-Sleep -Milliseconds 300
}

Add-Type -AssemblyName System.Windows.Forms
if (-not $NoSelectAll) {
  [System.Windows.Forms.SendKeys]::SendWait("^a")
  Start-Sleep -Milliseconds 80
}
[System.Windows.Forms.SendKeys]::SendWait("^v")
Start-Sleep -Milliseconds 250
