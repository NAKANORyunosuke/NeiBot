$path = 'D:\Workspace\Programming\Python\NeiBot\NginxAutoBan-Advanced.ps1'
$i=0
Get-Content $path | ForEach-Object {
  $i++
  $dq = ([regex]::Matches($_, '"')).Count
  $sq = ([regex]::Matches($_, "'")).Count
  if (($dq % 2 -eq 1) -or ($sq % 2 -eq 1)) { '{0,5}: {1}' -f $i, $_ }
}