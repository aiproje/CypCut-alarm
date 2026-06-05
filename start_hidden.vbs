' ============================================================
'  CypCut Monitor - Sessiz başlatma (VBScript)
'
'  Bu script konsol penceresi açmadan start.bat'ı çalıştırır.
'  Windows başlangıç klasörüne bu dosyanın kısayolunu koyun:
'    Win+R -> shell:startup
' ============================================================

Option Explicit

Dim shell, fso, currentDir
Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = currentDir

shell.Run """" & currentDir & "\start.bat""", 0, False

Set shell = Nothing
Set fso   = Nothing
