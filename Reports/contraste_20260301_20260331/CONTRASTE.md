# Contraste biomasa — 01/03/2026 a 31/03/2026

Mes de referencia para validacion con negocio. Premisas: `PREMISAS.md`.

## Resumen

| Metrica | kg |
|---------|---:|
| Entradas TINA | 665,081.58 |
| Salidas CAJA | 492,968.78 |
| TINA procesada | 429,619.00 |
| Stock inicial | 442,910.75 |
| Stock de entrada | 235,462.58 |
| Stock inventario cierre | 678,373.33 |
| Merma (E - S - Stock entrada) | -63,349.78 |
| % Merma / entradas | -9.53% |
| Balance TINA - CAJA | 172,112.80 |

## Arrastre

- Tinas arrastradas (mes anterior): **43** packs / **11,715.00** kg
- Periodo origen: 01/02/2026 a 28/02/2026

## Encadenamiento mensual

| Mes | Apertura | Entradas | Procesada | Salidas | Cierre |
|-----|----------:|----------:|----------:|----------:|----------:|
| 01/01/2026 | 0.00 | 571,996.04 | 379,157.00 | 446,162.12 | 192,839.04 |
| 01/02/2026 | 192,839.04 | 610,984.71 | 360,913.00 | 421,908.79 | 442,910.75 |

## Comprobaciones

| Comprobacion | Residual (kg) | OK |
|--------------|-------------:|:--:|
| Balance masa: Entrada = Salida + Stock entrada + Merma | 0.00 | Si |
| Stock de entrada = Entradas - TINA procesada | 0.00 | Si |
| Merma = Entradas - Salidas - Stock de entrada | 0.00 | Si |
| Merma = TINA procesada - Salidas CAJA | 0.00 | Si |
| Stock inventario = Stock inicial + Entradas - TINA procesada | 0.00 | Si |
| Balance TINA-CAJA = Entradas - Salidas | 0.00 | Si |

## Pendiente validacion negocio

- [ ] Confirmar stock inicial de apertura (arrastre desde enero)
- [ ] Confirmar stock inventario cierre con planta
- [ ] Interpretar merma negativa vs stock arrastrado
- [ ] Firmar mes como referencia antes de replicar a otros meses
