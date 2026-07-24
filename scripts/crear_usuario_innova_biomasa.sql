/*
  Usuario SQL solo-lectura para CALCULO_BIOMASA (Innova).

  Objetos necesarios:
    dbo.proc_packs, dbo.proc_materials, dbo.proc_matxacts, dbo.vw_stolt

  Uso (como sysadmin / sa):
    sqlcmd -S <servidor> -d Innova -U sa -P *** -i crear_usuario_innova_biomasa.sql

  O ejecutar con: python scripts/crear_usuario_innova_biomasa.py

  Variables a sustituir antes de ejecutar a mano:
    $(LOGIN)     ej. biomasa_ro
    $(PASSWORD)  contraseña fuerte
    $(DBNAME)    Innova
*/
USE [master];
GO

IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'$(LOGIN)')
BEGIN
  CREATE LOGIN [$(LOGIN)] WITH PASSWORD = N'$(PASSWORD)', CHECK_POLICY = ON, CHECK_EXPIRATION = OFF;
END
ELSE
BEGIN
  ALTER LOGIN [$(LOGIN)] WITH PASSWORD = N'$(PASSWORD)';
END
GO

USE [$(DBNAME)];
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'$(LOGIN)')
BEGIN
  CREATE USER [$(LOGIN)] FOR LOGIN [$(LOGIN)];
END
GO

-- Rol de solo lectura de base de datos (cubre tablas/vistas dbo usadas por el informe)
ALTER ROLE [db_datareader] ADD MEMBER [$(LOGIN)];
GO

-- Denegar escritura por si el login se añade a otros roles más adelante
DENY INSERT, UPDATE, DELETE, ALTER, CREATE TABLE, DROP TO [$(LOGIN)];
GO

PRINT N'Usuario $(LOGIN) listo en $(DBNAME) (db_datareader).';
GO
