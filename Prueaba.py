#!/usr/bin/env python3
"""
Módulo para descargar imágenes Sentinel-2 desde Copernicus Data Space Ecosystem con interfaz gráfica
Requiere: pip install requests tkinter geopandas shapely matplotlib rasterio
"""
import os
import zipfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime, timedelta
from pathlib import Path
import logging
import threading
import requests
import geopandas as gpd
from shapely.geometry import Polygon
import json
from urllib.parse import urlencode
import numpy as np
import rasterio
import matplotlib.pyplot as plt
from ndvi_processor import procesar_ndvi_por_tiles
from recorte_tlaxcala import recortar_ndvi_con_tlaxcala
from see_ndvi import visualizar_ndvi
from dateutil.relativedelta import relativedelta
import calendar

class TextHandler(logging.Handler):
    """Handler para mostrar logs en la interfaz gráfica"""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)
        def append():
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, msg + '\n')
            self.text_widget.configure(state='disabled')
            self.text_widget.see(tk.END)
        self.text_widget.after(0, append)

class CopernicusDownloader:
    def __init__(self, username, password, download_dir="./downloads", timeout=60):
        """
        Inicializar descargador de Copernicus Data Space Ecosystem
        
        Args:
            username: Usuario de Copernicus Data Space
            password: Contraseña de Copernicus Data Space
            download_dir: Directorio donde guardar las descargas
            timeout: Timeout en segundos para conexiones
        """
        self.username = username
        self.password = password
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)
        self.timeout = timeout
        self.access_token = None
        
        # URLs de la nueva API de Copernicus Data Space
        self.auth_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        self.catalog_url = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
        self.download_url = "https://zipper.dataspace.copernicus.eu/odata/v1/Products"
        
        # Área de interés para Tlaxcala (coordenadas aproximadas)
        self.tlaxcala_bounds = Polygon([
            (-98.8, 19.1),  # SO
            (-97.6, 19.1),  # SE
            (-97.6, 19.9),  # NE
            (-98.8, 19.9),  # NO
            (-98.8, 19.1)   # Cerrar polígono
        ])
        
        # Configurar logging
        self.logger = logging.getLogger(__name__)
    
    def get_access_token(self):
        """Obtener token de acceso de Copernicus Data Space"""
        try:
            data = {
                "grant_type": "password",
                "username": self.username,
                "password": self.password,  
                "client_id": "cdse-public"
            }
            
            response = requests.post(self.auth_url, data=data, timeout=self.timeout)
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data.get("access_token")
            
            if not self.access_token:
                raise Exception("No se pudo obtener el token de acceso")
                
            self.logger.info("✓ Token de acceso obtenido exitosamente")
            return True
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Error de autenticación: {str(e)}")
    
    def create_aoi_from_shapefile(self, shapefile_path):
        """Crear área de interés desde shapefile"""
        try:
            gdf = gpd.read_file(shapefile_path)
            if gdf.crs != 'EPSG:4326':
                gdf = gdf.to_crs('EPSG:4326')
            geometry = gdf.geometry.unary_union
            return geometry
        except Exception as e:
            self.logger.warning(f"No se pudo cargar shapefile: {e}. Usando bounds por defecto.")
            return self.tlaxcala_bounds
    
    def polygon_to_wkt(self, polygon):
        """Convertir polígono a formato WKT para la consulta"""
        coords = list(polygon.exterior.coords)
        wkt_coords = [f"{lon} {lat}" for lon, lat in coords]
        return f"POLYGON(({','.join(wkt_coords)}))"
    
    def search_sentinel2_products(self, start_date, end_date, aoi=None, max_cloud_cover=20, max_results=50):
        """Buscar productos Sentinel-2 usando la nueva API"""
        if aoi is None:
            aoi = self.tlaxcala_bounds
        
        # Asegurar que tenemos token de acceso
        if not self.access_token:
            self.get_access_token()
        
        bounds = aoi.bounds
        bbox_wkt = f"POLYGON(({bounds[0]} {bounds[1]},{bounds[2]} {bounds[1]},{bounds[2]} {bounds[3]},{bounds[0]} {bounds[3]},{bounds[0]} {bounds[1]}))"
        
        self.logger.info(f"Buscando productos Sentinel-2 del {start_date.strftime('%Y-%m-%d')} al {end_date.strftime('%Y-%m-%d')}")
        self.logger.info(f"Cobertura de nubes máxima: {max_cloud_cover}%")
        
        # Construir filtro de búsqueda para la nueva API
        filter_query = (
            f"Collection/Name eq 'SENTINEL-2' "
            f"and ContentDate/Start ge {start_date.strftime('%Y-%m-%d')}T00:00:00.000Z "
            f"and ContentDate/Start le {end_date.strftime('%Y-%m-%d')}T23:59:59.999Z "
            f"and Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and att/OData.CSC.StringAttribute/Value eq 'S2MSI2A') "
            f"and Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value le {max_cloud_cover}) "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;{bbox_wkt}')"
        )
        
        params = {
            "$filter": filter_query,
            "$orderby": "ContentDate/Start desc",
            "$top": max_results
        }
        
        headers = {
            "Authorization": f"Bearer {self.access_token}"
        }
        
        try:
            response = requests.get(self.catalog_url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            
            products_data = response.json()
            products = products_data.get("value", [])
            
            self.logger.info(f"Encontrados {len(products)} productos")
            
            # Convertir a formato más amigable
            products_list = []
            for product in products:
                # Extraer información relevante
                product_info = {
                    'id': product['Id'],
                    'title': product['Name'],
                    'date': product['ContentDate']['Start'][:10],
                    'cloud_cover': 0  # Valor por defecto
                }
                
                # Buscar cobertura de nubes en los atributos
                for attr in product.get('Attributes', []):
                    if attr.get('Name') == 'cloudCover':
                        product_info['cloud_cover'] = float(attr.get('Value', 0))
                        break
                
                products_list.append(product_info)
                self.logger.info(f"  {product_info['title']} - Fecha: {product_info['date']} - Nubes: {product_info['cloud_cover']:.1f}%")
            
            # Ordenar por fecha y cobertura de nubes
            products_list.sort(key=lambda x: (x['date'], x['cloud_cover']), reverse=True)
            
            return products_list
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Error al buscar productos: {str(e)}")
    
    def download_product(self, product_id, product_title):
        """Descargar un producto específico con renovación de token si es necesario"""

        def renovar_token_si_necesario():
            try:
                headers = {"Authorization": f"Bearer {self.access_token}"}
                test_url = f"{self.download_url}({product_id})"
                test_response = requests.head(test_url, headers=headers, timeout=self.timeout)
                if test_response.status_code == 401:
                    self.logger.info("Token expirado. Renovando...")
                    self.get_access_token()
            except Exception as e:
                self.logger.warning(f"No se pudo verificar el token: {e}. Renovando por seguridad.")
                self.get_access_token()

        # Verificar token antes de la descarga
        if not self.access_token:
            self.get_access_token()
        else:
            renovar_token_si_necesario()

        download_endpoint = f"{self.download_url}({product_id})/$value"
        headers = {
            "Authorization": f"Bearer {self.access_token}"
        }

        filename = f"{product_title}.zip"
        filepath = self.download_dir / filename

        self.logger.info(f"Descargando: {product_title}")

        try:
            with requests.get(download_endpoint, headers=headers, stream=True, timeout=self.timeout) as response:
                response.raise_for_status()
                total_size = int(response.headers.get('content-length', 0))

                with open(filepath, 'wb') as f:
                    downloaded = 0
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0 and downloaded % (1024 * 1024) == 0:
                                progress = (downloaded / total_size) * 100
                                self.logger.info(f"Progreso: {progress:.1f}%")

            self.logger.info(f"Descarga completada: {filepath}")
            return str(filepath)

        except Exception as e:
            self.logger.error(f"Error descargando {product_title}: {e}")
            if filepath.exists():
                filepath.unlink()  # Eliminar archivo incompleto
            raise
    
    def extract_and_organize_bands(self, zip_files, target_bands=['TCI', 'B08', 'B04', 'B03']):
        """Extraer y organizar bandas específicas - VERSIÓN MEJORADA para Windows"""
        organized_bands = {}
        
        for zip_file in zip_files:
            self.logger.info(f"Extrayendo bandas de: {zip_file}")
            
            # Crear directorio de extracción con nombre más corto para evitar problemas de ruta larga
            zip_path = Path(zip_file)
            # Usar solo el tile ID y fecha para el directorio
            safe_name = zip_path.stem
            tile_part = None
            date_part = None
            
            # Extraer partes importantes del nombre
            parts = safe_name.split('_')
            for part in parts:
                if part.startswith('T') and len(part) == 6:
                    tile_part = part
                elif len(part) >= 8 and part.startswith('2'):
                    date_part = part[:8]
            
            # Crear nombre corto para el directorio
            if tile_part and date_part:
                short_name = f"{tile_part}_{date_part}"
            else:
                short_name = safe_name[:50]  # Truncar si es muy largo
            
            extract_dir = self.download_dir / short_name
            
            # Limpiar directorio si existe
            if extract_dir.exists():
                import shutil
                try:
                    shutil.rmtree(extract_dir)
                except OSError as e:
                    self.logger.warning(f"No se pudo limpiar directorio existente: {e}")
            
            extract_dir.mkdir(exist_ok=True)
            
            try:
                with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                    file_list = zip_ref.namelist()
                    
                    # Contar archivos de bandas objetivo
                    band_count = 0
                    for f in file_list:
                        if f.endswith('.jp2'):
                            for band in target_bands:
                                if f"_{band}_" in f:
                                    band_count += 1
                                    break
                    
                    self.logger.info(f"  Archivos de bandas objetivo encontrados: {band_count}")
                    
                    # Extraer solo los archivos necesarios para evitar problemas de ruta larga
                    band_files = {}
                    extracted_files = []
                    
                    for file_path in file_list:
                        if file_path.endswith('.jp2'):
                            # Verificar si es una banda objetivo
                            for band in target_bands:
                                if f"_{band}_" in file_path:
                                    try:
                                        # Extraer archivo individual
                                        zip_ref.extract(file_path, extract_dir)
                                        
                                        # Construir ruta completa del archivo extraído
                                        extracted_file_path = extract_dir / file_path
                                        
                                        if extracted_file_path.exists():
                                            band_files[band] = str(extracted_file_path.resolve())
                                            extracted_files.append(str(extracted_file_path))
                                            
                                            # Obtener solo el nombre del archivo para el log
                                            file_name = Path(file_path).name
                                            self.logger.info(f"  ✓ Extraída banda {band}: {file_name}")
                                        else:
                                            self.logger.warning(f"  ✗ No se pudo extraer banda {band}: {file_path}")
                                            
                                    except Exception as extract_error:
                                        self.logger.error(f"  ✗ Error extrayendo {file_path}: {extract_error}")
                                        continue
                                    break
                    
                    # Si se encontraron bandas, organizar por tile
                    if band_files:
                        product_name = safe_name
                        tile_id = self._extract_tile_id(product_name)
                        
                        if tile_id:
                            organized_bands[tile_id] = {
                                'date': self._extract_date(product_name),
                                'bands': band_files,
                                'product_name': product_name,
                                'extract_dir': str(extract_dir),
                                'extracted_files': extracted_files
                            }
                            self.logger.info(f"  ✓ Procesado tile {tile_id} con {len(band_files)} bandas: {list(band_files.keys())}")
                        else:
                            self.logger.warning(f"  ✗ No se pudo extraer tile ID de: {product_name}")
                    else:
                        self.logger.warning(f"  ✗ No se encontraron bandas objetivo en: {zip_path.name}")
                        
                        # Debug: mostrar archivos JP2 disponibles
                        jp2_files = [f for f in file_list if f.endswith('.jp2')]
                        self.logger.info(f"  Archivos JP2 disponibles en ZIP: {len(jp2_files)}")
                        
                        # Mostrar algunos ejemplos
                        for jp2_file in jp2_files[:5]:
                            file_name = Path(jp2_file).name
                            self.logger.info(f"    {file_name}")
                        
                        if len(jp2_files) > 5:
                            self.logger.info(f"    ... y {len(jp2_files) - 5} archivos más")
            
            except zipfile.BadZipFile:
                self.logger.error(f"Archivo ZIP corrupto: {zip_file}")
                continue
            except PermissionError as e:
                self.logger.error(f"Error de permisos extrayendo {zip_file}: {e}")
                continue
            except Exception as e:
                self.logger.error(f"Error general extrayendo {zip_file}: {e}")
                self.logger.error(f"Tipo de error: {type(e).__name__}")
                
                # Información adicional para debugging
                if "path too long" in str(e).lower() or len(str(extract_dir)) > 200:
                    self.logger.error("Problema con rutas largas detectado.")
                    self.logger.error(f"Longitud de ruta base: {len(str(extract_dir))} caracteres")
                    self.logger.error("Soluciones:")
                    self.logger.error("1. Mover los archivos a C:\\temp o similar")
                    self.logger.error("2. Usar un directorio de descarga más corto")
                
                continue
        
        return organized_bands


    def cleanup_extracted_files(self, organized_bands):
        """Limpiar archivos extraídos temporales (opcional)"""
        for tile_id, data in organized_bands.items():
            extract_dir = Path(data.get('extract_dir', ''))
            if extract_dir.exists():
                import shutil
                try:
                    shutil.rmtree(extract_dir)
                    self.logger.info(f"Limpiado directorio temporal: {extract_dir}")
                except Exception as e:
                    self.logger.warning(f"No se pudo limpiar {extract_dir}: {e}")

    def _extract_tile_id(self, product_name):
        """Extraer tile ID del nombre del producto"""
        parts = product_name.split('_')
        for part in parts:
            if part.startswith('T') and len(part) == 6:
                return part
        return None
    
    def _extract_date(self, product_name):
        """Extraer fecha del nombre del producto"""
        parts = product_name.split('_')
        for part in parts:
            if len(part) >= 8 and part.startswith('2'):
                try:
                    return datetime.strptime(part[:8], '%Y%m%d')
                except:
                    continue
        return datetime.now()
    
    def test_connection(self):
        """Probar la conexión con la nueva API de Copernicus"""
        try:
            self.logger.info("Probando conexión con Copernicus Data Space Ecosystem...")
            
            # Probar autenticación
            self.get_access_token()
            
            # Probar una consulta simple
            small_area = Polygon([
                (-98.0, 19.3), (-98.0, 19.4), (-97.9, 19.4), (-97.9, 19.3), (-98.0, 19.3)
            ])
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
            
            products = self.search_sentinel2_products(start_date, end_date, small_area, max_results=1)
            
            self.logger.info(f"✓ Conexión exitosa con Copernicus Data Space")
            self.logger.info(f"✓ Productos encontrados en consulta de prueba: {len(products)}")
            
            return True, f"Conexión establecida correctamente\nProductos encontrados: {len(products)}"
            
        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Error de conexión: {error_msg}")
            return False, error_msg
    
    def download_for_period(self, start_date, end_date, shapefile_path=None, max_cloud_cover=20, max_products=5):
        """Descargar imágenes para un período específico"""
        try:
            # Autenticar primero
            self.get_access_token()
            
            aoi = None
            if shapefile_path:
                aoi = self.create_aoi_from_shapefile(shapefile_path)
            
            products = self.search_sentinel2_products(start_date, end_date, aoi, max_cloud_cover, max_products)
            
            if not products:
                self.logger.warning(f"No se encontraron productos para el período seleccionado")
                return {}
            
            downloaded_files = []
            
            # Descargar los productos seleccionados
            for i, product in enumerate(products[:max_products]):
                try:
                    downloaded_file = self.download_product(product['id'], product['title'])
                    downloaded_files.append(downloaded_file)
                except Exception as e:
                    self.logger.error(f"Error descargando producto {i+1}: {e}")
                    continue
            
            if not downloaded_files:
                self.logger.warning("No se pudieron descargar productos")
                return {}
            
            organized_bands = self.extract_and_organize_bands(downloaded_files)
            return organized_bands

        except Exception as e:
            self.logger.error(f"Error en download_for_period: {e}")
            raise

class SentinelDownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Descargador de Imágenes Sentinel-2 (Copernicus Data Space)")
        self.root.geometry("800x700")
        
        self.downloader = None
        self.shapefile_path = None
        
        self.setup_ui()
        self.setup_logging()
    
    def setup_ui(self):
        """Configurar la interfaz de usuario"""
        
        # Frame principal con pestañas
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Pestaña de configuración
        config_frame = ttk.Frame(notebook)
        notebook.add(config_frame, text="Configuración")
        
        # Nota sobre la nueva API
        info_frame = ttk.LabelFrame(config_frame, text="Información Importante")
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        
        info_text = ("Este descargador usa la nueva API de Copernicus Data Space Ecosystem.\n"
                    "Regístrate gratis en: https://dataspace.copernicus.eu/\n"
                    "La registración en el hub antiguo NO funciona para esta nueva API.")
        ttk.Label(info_frame, text=info_text, foreground='blue', font=('Arial', 9)).pack(padx=5, pady=5)
        
        # Credenciales
        cred_frame = ttk.LabelFrame(config_frame, text="Credenciales de Copernicus Data Space")
        cred_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(cred_frame, text="Usuario:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.username_entry = ttk.Entry(cred_frame, width=30)
        self.username_entry.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(cred_frame, text="Contraseña:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.password_entry = ttk.Entry(cred_frame, width=30, show="*")
        self.password_entry.grid(row=1, column=1, padx=5, pady=5)
        
        # Configuración de conexión
        conn_frame = ttk.LabelFrame(config_frame, text="Configuración de Conexión")
        conn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Timeout
        ttk.Label(conn_frame, text="Timeout (seg):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.timeout_var = tk.StringVar(value="120")
        ttk.Entry(conn_frame, textvariable=self.timeout_var, width=10).grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)
        
        # Directorio de descarga
        dir_frame = ttk.LabelFrame(config_frame, text="Directorio de Descarga")
        dir_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.download_dir_var = tk.StringVar(value="./downloads")
        ttk.Entry(dir_frame, textvariable=self.download_dir_var, width=50).pack(side=tk.LEFT, padx=5, pady=5)
        ttk.Button(dir_frame, text="Examinar", command=self.select_download_dir).pack(side=tk.RIGHT, padx=5, pady=5)
        
        # Área de interés
        aoi_frame = ttk.LabelFrame(config_frame, text="Área de Interés (Opcional)")
        aoi_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.shapefile_var = tk.StringVar()
        ttk.Entry(aoi_frame, textvariable=self.shapefile_var, width=50).pack(side=tk.LEFT, padx=5, pady=5)
        ttk.Button(aoi_frame, text="Seleccionar Shapefile", command=self.select_shapefile).pack(side=tk.RIGHT, padx=5, pady=5)
        
        # Pestaña de descarga
        download_frame = ttk.Frame(notebook)
        notebook.add(download_frame, text="Descarga")
        
        # Parámetros de búsqueda
        params_frame = ttk.LabelFrame(download_frame, text="Parámetros de Búsqueda")
        params_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Fechas
        date_frame = ttk.Frame(params_frame)
        date_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(date_frame, text="Fecha inicial:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.start_date_var = tk.StringVar(value="2024-05-01")
        ttk.Entry(date_frame, textvariable=self.start_date_var, width=15).grid(row=0, column=1, padx=5)
        
        ttk.Label(date_frame, text="Fecha final:").grid(row=0, column=2, sticky=tk.W, padx=5)
        self.end_date_var = tk.StringVar(value="2024-05-31")
        ttk.Entry(date_frame, textvariable=self.end_date_var, width=15).grid(row=0, column=3, padx=5)
        
        # Otros parámetros
        other_params = ttk.Frame(params_frame)
        other_params.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(other_params, text="Máx. cobertura nubes (%):").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.cloud_cover_var = tk.StringVar(value="20")
        ttk.Entry(other_params, textvariable=self.cloud_cover_var, width=10).grid(row=0, column=1, padx=5)
        
        ttk.Label(other_params, text="Máx. productos:").grid(row=0, column=2, sticky=tk.W, padx=5)
        self.max_products_var = tk.StringVar(value="3")
        ttk.Entry(other_params, textvariable=self.max_products_var, width=10).grid(row=0, column=3, padx=5)
        
        # Botones de control
        control_frame = ttk.Frame(download_frame)
        control_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.download_btn = ttk.Button(control_frame, text="Iniciar Descarga", command=self.start_download)
        self.download_btn.pack(side=tk.LEFT, padx=5)
        
        self.test_btn = ttk.Button(control_frame, text="Probar Conexión", command=self.test_connection)
        self.test_btn.pack(side=tk.LEFT, padx=5)
        
        # Barra de progreso
        self.progress = ttk.Progressbar(download_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, padx=10, pady=5)
        
        # Log de salida
        log_frame = ttk.LabelFrame(download_frame, text="Log de Actividad")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def setup_logging(self):
        """Configurar el sistema de logging"""
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        
        # Handler para mostrar logs en la interfaz
        text_handler = TextHandler(self.log_text)
        text_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        logger = logging.getLogger()
        logger.addHandler(text_handler)
    
    def select_download_dir(self):
        """Seleccionar directorio de descarga"""
        directory = filedialog.askdirectory()
        if directory:
            self.download_dir_var.set(directory)
    
    def select_shapefile(self):
        """Seleccionar archivo shapefile"""
        filename = filedialog.askopenfilename(
            title="Seleccionar Shapefile",
            filetypes=[("Shapefiles", "*.shp"), ("Todos los archivos", "*.*")]
        )
        if filename:
            self.shapefile_var.set(filename)
    
    def test_connection(self):
        """Probar la conexión con Copernicus"""
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        
        if not username or not password:
            messagebox.showerror("Error", "Por favor ingrese usuario y contraseña")
            return
        
        def test_thread():
            try:
                self.progress.start()
                
                timeout = int(self.timeout_var.get())
                
                # Crear instancia del descargador con las nuevas credenciales
                downloader = CopernicusDownloader(username, password, timeout=timeout)
                
                success, message = downloader.test_connection()
                
                if success:
                    messagebox.showinfo("Éxito", message)
                else:
                    messagebox.showerror("Error de Conexión", message)
                    
            except Exception as e:
                logging.error(f"Error inesperado durante la prueba: {e}")
                messagebox.showerror("Error", f"Error inesperado:\n{str(e)}")
            finally:
                self.progress.stop()
        
        threading.Thread(target=test_thread, daemon=True).start()
    
    def start_download(self):
        """Iniciar proceso de descarga"""
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
    
        if not username or not password:
            messagebox.showerror("Error", "Por favor ingrese usuario y contraseña")
            return
    
        try:
            start_date = datetime.strptime(self.start_date_var.get(), "%Y-%m-%d")
            end_date = datetime.strptime(self.end_date_var.get(), "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Error", "Formato de fecha inválido. Use YYYY-MM-DD")
            return
    
        try:
            timeout = int(self.timeout_var.get())
            if timeout < 30:
                messagebox.showerror("Error", "El timeout debe ser al menos 30 segundos")
                return
        except ValueError:
            messagebox.showerror("Error", "Timeout debe ser un número entero")
            return
    
        try:
            max_cloud_cover = float(self.cloud_cover_var.get())
            max_products = int(self.max_products_var.get())
        except ValueError:
            messagebox.showerror("Error", "Parámetros numéricos inválidos")
            return
    
        def download_thread():
            try:
                self.progress.start()
                self.download_btn.config(state='disabled')

                self.downloader = CopernicusDownloader(
                    username, password, self.download_dir_var.get(), timeout
                )

                shapefile_path = self.shapefile_var.get() if self.shapefile_var.get() else None

                current_start = start_date

                while current_start < end_date:
                    # Calcular fin de mes
                    last_day = calendar.monthrange(current_start.year, current_start.month)[1]
                    current_end = datetime(
                        current_start.year, current_start.month, last_day
                    )

                    # Limitar al rango real
                    if current_end > end_date:
                        current_end = end_date

                    periodo_tag = current_start.strftime("%Y%m")  # ej. 202405

                    logging.info(f"Procesando periodo: {current_start.date()} - {current_end.date()}")

                    organized_bands = self.downloader.download_for_period(
                        current_start, current_end, shapefile_path, max_cloud_cover, max_products
                    )

                    if organized_bands:
                        logging.info("✓ Descarga completada exitosamente")
                        procesar_ndvi_por_tiles(
                            organized_bands,
                            output_folder=f"NDVI_output_{periodo_tag}"
                        )

                        # Recorte
                        try:
                            zip_path = "Estados_Mexico.zip"
                            ndvi_mosaico = f"NDVI_output_{periodo_tag}/NDVI_MOSAICO.tif"
                            salida_recorte = f"NDVI_output_{periodo_tag}/NDVI_TLAXCALA.tif"
                            recortar_ndvi_con_tlaxcala(zip_path, ndvi_mosaico, salida_recorte)
                            logging.info(f"✓ Recorte para Tlaxcala: {salida_recorte}")
                        except Exception as e:
                            logging.warning(f"No se pudo recortar Tlaxcala: {e}")

                        # Visualizar
                        try:
                            visualizar_ndvi(
                                ndvi_path=salida_recorte,
                                fecha_inicio=current_start.date(),
                                fecha_fin=current_end.date()
                            )
                        except Exception as e:
                            logging.warning(f"No se pudo visualizar NDVI: {e}")

                    else:
                        logging.warning(f"No se descargaron productos en {current_start.date()} - {current_end.date()}")

                    # Avanzar al siguiente mes
                    current_start = current_end + relativedelta(days=1)

                messagebox.showinfo("Éxito", f"NDVI procesado y recortado para múltiples periodos.")

            except Exception as e:
                logging.error(f"Error durante la descarga: {e}")
                messagebox.showerror("Error", f"Error durante la descarga:\n{str(e)}")
            finally:
                self.progress.stop()
                self.download_btn.config(state='normal')
            
        threading.Thread(target=download_thread, daemon=True).start()

def main():
    """Función principal"""
    root = tk.Tk()
    app = SentinelDownloaderGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()