# Scripts

| Script | Uso |
|--------|-----|
| `crear_usuario_innova_biomasa.py` | Alta/actualización login SQL solo-lectura en Innova |
| `crear_usuario_innova_biomasa.sql` | Misma lógica en T-SQL (sqlcmd) |
| `preparar_distribucion_usuarios.py` | Regenera `distribucion/AEV` y `distribucion/JUY` |

Ejemplo:

```bat
python scripts/crear_usuario_innova_biomasa.py --login AEV --update-env
python scripts/preparar_distribucion_usuarios.py
```

Ver [INSTRUCCIONES.md](../INSTRUCCIONES.md) y [FUNCIONAMIENTO.md](../FUNCIONAMIENTO.md).
