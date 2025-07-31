import rasterio
import matplotlib.pyplot as plt
import numpy as np

def visualizar_ndvi(ndvi_path, fecha_inicio, fecha_fin):
    """
    Muestra el NDVI recortado con el periodo de fechas en el título.
    
    Args:
        ndvi_path: Ruta al archivo TIFF NDVI recortado.
        fecha_inicio: Fecha inicial como string, ej. "2024-05-01"
        fecha_fin: Fecha final como string, ej. "2024-05-31"
    """
    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)

        # Enmascarar valores fuera de [-1, 1]
        ndvi = np.where((ndvi >= -1.0) & (ndvi <= 1.0), ndvi, np.nan)

        plt.figure(figsize=(10, 8))
        cmap = plt.cm.YlGn  # Escala verde
        p2, p98 = np.nanpercentile(ndvi, (2, 98))
        plt.imshow(ndvi, cmap=plt.cm.YlGn, vmin=p2, vmax=p98)
        plt.colorbar(label='NDVI')
        plt.title(f"NDVI Recortado (TLAXCALA)\nPeriodo: {fecha_inicio} a {fecha_fin}")
        plt.axis('off')
        plt.tight_layout()
        plt.show()
