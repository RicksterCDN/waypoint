<#
.SYNOPSIS
Opens Caliber Tampermonkey userscripts for idempotent install/update in Edge.

.DESCRIPTION
Tampermonkey uses a userscript's @name and @namespace metadata as its identity.
Serving the checked-in *.user.js files and opening those URLs in Edge lets
Tampermonkey create missing scripts or replace existing scripts with the same
identity after you confirm the install/update page.

This script intentionally does not write directly to Tampermonkey's browser
extension storage. Browser extension stores are profile- and version-specific,
and bypassing Tampermonkey's installer is brittle.

.EXAMPLE
.\scripts\import-tampermonkey-userscripts.ps1

.EXAMPLE
.\scripts\import-tampermonkey-userscripts.ps1 -EdgeProfile "Profile 1"

.EXAMPLE
.\scripts\import-tampermonkey-userscripts.ps1 -DefaultBrowser

.EXAMPLE
.\scripts\import-tampermonkey-userscripts.ps1 -Path .\scripts\m365-agents-add-contract-policy-expert.user.js
#>

[CmdletBinding()]
param(
    [Parameter(ValueFromPipeline = $true, ValueFromPipelineByPropertyName = $true)]
    [string[]] $Path,

    [string] $EdgeProfile = "Default",

    [int] $Port = 8765,

    [int] $KeepAliveSeconds = 600,

    [string] $EdgePath,

    [switch] $DefaultBrowser,

    [switch] $NoLaunch
)

begin {
    Set-StrictMode -Version Latest
    $ErrorActionPreference = "Stop"

    function Get-RepoRoot {
        $scriptDirectory = Split-Path -Parent $PSCommandPath
        $candidate = Resolve-Path (Join-Path $scriptDirectory "..")
        $gitDirectory = Join-Path $candidate ".git"
        if (Test-Path $gitDirectory) {
            return $candidate.Path
        }

        $gitRoot = & git -C $scriptDirectory rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -eq 0 -and $gitRoot) {
            return $gitRoot.Trim()
        }

        throw "Could not determine repository root from $scriptDirectory."
    }

    function Resolve-UserscriptPath {
        param([string] $CandidatePath)

        $resolved = Resolve-Path $CandidatePath
        if (-not $resolved) {
            throw "Userscript not found: $CandidatePath"
        }

        $item = Get-Item $resolved.Path
        if ($item.PSIsContainer -or $item.Name -notlike "*.user.js") {
            throw "Expected a .user.js file, got: $($item.FullName)"
        }

        return $item
    }

    function Get-UserscriptMetadata {
        param([System.IO.FileInfo] $File)

        $content = Get-Content -LiteralPath $File.FullName -Raw
        $name = [regex]::Match($content, "(?m)^//\s+@name\s+(.+?)\s*$").Groups[1].Value
        $namespace = [regex]::Match($content, "(?m)^//\s+@namespace\s+(.+?)\s*$").Groups[1].Value

        if (-not $name -or -not $namespace) {
            throw "Missing @name or @namespace metadata in $($File.FullName)."
        }

        [PSCustomObject]@{
            File = $File
            Name = $name
            Namespace = $namespace
            Identity = "$namespace::$name"
            Route = "/" + [Uri]::EscapeDataString($File.Name)
        }
    }

    function Find-Edge {
        param([string] $PreferredPath)

        if ($PreferredPath) {
            if (Test-Path $PreferredPath) {
                return (Resolve-Path $PreferredPath).Path
            }
            throw "Edge executable not found at: $PreferredPath"
        }

        $candidates = @(
            "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
        )

        foreach ($candidate in $candidates) {
            if (Test-Path $candidate) {
                return $candidate
            }
        }

        $command = Get-Command msedge -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }

        throw "Microsoft Edge was not found. Pass -EdgePath to the Edge executable."
    }

    function Write-Response {
        param(
            [System.Net.HttpListenerContext] $Context,
            [int] $StatusCode,
            [string] $Body,
            [string] $ContentType = "text/plain; charset=utf-8"
        )

        $bytes = [Text.Encoding]::UTF8.GetBytes($Body)
        $Context.Response.StatusCode = $StatusCode
        $Context.Response.ContentType = $ContentType
        $Context.Response.Headers["Cache-Control"] = "no-store, max-age=0"
        $Context.Response.Headers["X-Content-Type-Options"] = "nosniff"
        $Context.Response.ContentLength64 = $bytes.Length
        $Context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        $Context.Response.OutputStream.Close()
    }

    $repoRoot = Get-RepoRoot
    $inputPaths = New-Object System.Collections.Generic.List[string]
}

process {
    foreach ($candidatePath in $Path) {
        if ($candidatePath) {
            $inputPaths.Add($candidatePath)
        }
    }
}

end {
    if ($inputPaths.Count -eq 0) {
        $defaultScriptDirectory = Join-Path $repoRoot "scripts"
        Get-ChildItem -LiteralPath $defaultScriptDirectory -Filter "*.user.js" |
            Sort-Object Name |
            ForEach-Object { $inputPaths.Add($_.FullName) }
    }

    if ($inputPaths.Count -eq 0) {
        throw "No .user.js files were found."
    }

    $scripts = foreach ($candidatePath in $inputPaths) {
        Get-UserscriptMetadata (Resolve-UserscriptPath $candidatePath)
    }

    $duplicate = $scripts |
        Group-Object Identity |
        Where-Object Count -gt 1 |
        Select-Object -First 1
    if ($duplicate) {
        throw "Multiple userscripts have the same Tampermonkey identity: $($duplicate.Name)"
    }

    $routes = @{}
    foreach ($script in $scripts) {
        $routes[$script.Route] = $script
    }

    $prefix = "http://127.0.0.1:$Port/"
    $listener = [System.Net.HttpListener]::new()
    $listener.Prefixes.Add($prefix)

    try {
        $listener.Start()
    } catch {
        throw "Could not listen on $prefix. Choose another -Port or close the process using this port. $($_.Exception.Message)"
    }

    try {
        $installUrls = foreach ($script in $scripts) {
            "$prefix$($script.Route.TrimStart('/'))?v=$([Uri]::EscapeDataString((Get-Item $script.File.FullName).LastWriteTimeUtc.Ticks.ToString()))"
        }

        Write-Host "Serving userscripts from $prefix"
        foreach ($script in $scripts) {
            Write-Host " - $($script.Name) [$($script.Namespace)]"
        }

        if ($DefaultBrowser -and $EdgePath) {
            throw "Use either -DefaultBrowser or -EdgePath, not both."
        }

        if ($DefaultBrowser -and $EdgeProfile -ne "Default") {
            throw "Use either -DefaultBrowser or -EdgeProfile, not both."
        }

        if ($DefaultBrowser -and $NoLaunch) {
            throw "Use either -DefaultBrowser or -NoLaunch, not both."
        }

        if (-not $NoLaunch -and $DefaultBrowser) {
            foreach ($installUrl in $installUrls) {
                Start-Process $installUrl
            }
            Write-Host "Opened the userscript URLs in your default browser. Confirm each Tampermonkey install/update tab."
        } elseif (-not $NoLaunch) {
            $edgeExecutable = Find-Edge $EdgePath
            $edgeArguments = @("--profile-directory=$EdgeProfile") + $installUrls
            Start-Process -FilePath $edgeExecutable -ArgumentList $edgeArguments
            Write-Host "Opened Edge profile '$EdgeProfile'. Confirm each Tampermonkey install/update tab."
        } else {
            Write-Host "Open these URLs in an Edge profile with Tampermonkey installed:"
            $installUrls | ForEach-Object { Write-Host " - $_" }
        }

        Write-Host "Keeping the local userscript server alive for $KeepAliveSeconds seconds. Press Ctrl+C when done."
        $deadline = [DateTime]::UtcNow.AddSeconds($KeepAliveSeconds)

        while ([DateTime]::UtcNow -lt $deadline) {
            $contextTask = $listener.GetContextAsync()
            while (-not $contextTask.IsCompleted -and [DateTime]::UtcNow -lt $deadline) {
                Start-Sleep -Milliseconds 100
            }

            if (-not $contextTask.IsCompleted) {
                continue
            }

            $context = $contextTask.GetAwaiter().GetResult()
            $requestPath = [Uri]::UnescapeDataString($context.Request.Url.AbsolutePath)
            if (-not $routes.ContainsKey($requestPath)) {
                Write-Response $context 404 "No userscript is registered for $requestPath."
                continue
            }

            $script = $routes[$requestPath]
            $body = Get-Content -LiteralPath $script.File.FullName -Raw
            Write-Response $context 200 $body "application/javascript; charset=utf-8"
        }
    } finally {
        if ($listener.IsListening) {
            $listener.Stop()
        }
        $listener.Close()
    }
}
