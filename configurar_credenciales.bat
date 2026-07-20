@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo  CALCULO_BIOMASA - Credenciales (.env)
echo ========================================
echo.
echo Las credenciales van fijas en el fichero .env
echo de esta carpeta. No se preguntan en pantalla.
echo.

if not exist ".env" (
  if exist ".env.example" (
    copy /Y ".env.example" ".env" >nul
    echo [OK] Se ha creado .env desde .env.example
  ) else (
    echo [ERROR] No existe .env.example para crear la plantilla.
    exit /b 1
  )
) else (
  echo [INFO] Ya existe: %~dp0.env
)

echo.
echo Edite DB_USER, DB_PASSWORD, BC_* etc. en:
echo   %~dp0.env
echo.
echo Abriendo .env con el Bloc de notas...
echo Guarde el fichero y cierre el Bloc de notas cuando termine.
echo.
start /wait notepad.exe "%~dp0.env"

echo.
echo [OK] Listo. Ya puede generar informes ^(opcion 3 del menu^).
echo.
pause
endlocal
exit /b 0
