"""Generate terraen_model_dokumentation.pdf using fpdf2 with Arial Unicode font."""
from fpdf import FPDF, XPos, YPos
import os

OUT       = os.path.join(os.path.dirname(__file__), "terraen_model_dokumentation.pdf")
FONT_DIR  = r"C:\Windows\Fonts"
ARIAL     = os.path.join(FONT_DIR, "arial.ttf")
ARIAL_B   = os.path.join(FONT_DIR, "arialbd.ttf")
ARIAL_I   = os.path.join(FONT_DIR, "ariali.ttf")
ARIAL_BI  = os.path.join(FONT_DIR, "arialbi.ttf")
COURIER   = os.path.join(FONT_DIR, "cour.ttf")
COURIER_B = os.path.join(FONT_DIR, "courbd.ttf")

BLUE  = (0,   70, 140)
GRAY  = (100, 100, 100)
LIGHT = (240, 244, 248)
BLACK = (20,  20,  20)


class Doc(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(30, 25, 25)
        self.set_auto_page_break(auto=True, margin=20)
        self.add_font("Arial",    style="",   fname=ARIAL)
        self.add_font("Arial",    style="B",  fname=ARIAL_B)
        self.add_font("Arial",    style="I",  fname=ARIAL_I)
        self.add_font("Arial",    style="BI", fname=ARIAL_BI)

    def header(self):
        if self.page == 1:
            return
        self.set_font("Arial", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 6, "Terraen_model - Teknisk dokumentation", align="L")
        self.cell(0, 6, "Vaadomraade Modeller", align="R",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*GRAY)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)

    def footer(self):
        if self.page == 1:
            return
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 6, f"Side {self.page_no()}", align="C")

    def title_page(self):
        self.add_page()
        self.set_fill_color(*BLUE)
        self.rect(0, 0, self.w, 60, "F")
        self.set_y(15)
        self.set_font("Arial", "B", 32)
        self.set_text_color(255, 255, 255)
        self.cell(0, 14, "Terraen_model", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Arial", "", 16)
        self.cell(0, 10, "Teknisk dokumentation", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_y(80)
        self.set_font("Arial", "B", 13)
        self.set_text_color(*BLUE)
        self.cell(0, 8, "Vaadomraade Modeller", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Arial", "", 11)
        self.set_text_color(*GRAY)
        self.cell(0, 7, "QGIS Processing Model Builder", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_y(230)
        self.set_font("Arial", "I", 9)
        self.cell(0, 6, "Genereret: Maj 2026", align="C")

    def h1(self, txt):
        self.ln(6)
        self.set_font("Arial", "B", 14)
        self.set_text_color(*BLUE)
        self.cell(0, 8, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*BLUE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)
        self.set_text_color(*BLACK)

    def h2(self, txt):
        self.ln(3)
        self.set_font("Arial", "B", 11)
        self.set_text_color(*BLUE)
        self.cell(0, 7, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)
        self.set_text_color(*BLACK)

    def body(self, txt):
        self.set_font("Arial", "", 10)
        self.set_text_color(*BLACK)
        self.set_x(self.l_margin)
        self.multi_cell(self.epw, 5.5, txt)
        self.ln(1)

    def kv(self, key, val):
        w_key = 52
        w_val = self.epw - w_key
        y0 = self.get_y()
        self.set_xy(self.l_margin, y0)
        self.set_font("Courier", "", 9.5)
        self.set_text_color(*BLUE)
        self.multi_cell(w_key, 5.5, key, new_x=XPos.RIGHT, new_y=YPos.TOP)
        h_key = self.get_y() - y0
        self.set_xy(self.l_margin + w_key, y0)
        self.set_font("Arial", "", 9.5)
        self.set_text_color(*BLACK)
        self.multi_cell(w_val, 5.5, val)
        h_val = self.get_y() - y0
        self.set_y(y0 + max(h_key, h_val))

    def code(self, txt):
        self.set_fill_color(*LIGHT)
        self.set_font("Courier", "", 9)
        self.set_text_color(60, 30, 90)
        self.set_x(self.l_margin)
        self.multi_cell(self.epw, 5, txt, fill=True)
        self.ln(2)
        self.set_text_color(*BLACK)

    def bullet(self, items, indent=5):
        self.set_font("Arial", "", 10)
        self.set_text_color(*BLACK)
        w_b = 4
        w_text = self.epw - indent - w_b
        for item in items:
            y0 = self.get_y()
            self.set_xy(self.l_margin + indent, y0)
            self.cell(w_b, 5.5, "•")
            self.set_xy(self.l_margin + indent + w_b, y0)
            self.multi_cell(w_text, 5.5, item)
        self.ln(1)

    def numbered(self, items, indent=5):
        self.set_font("Arial", "", 10)
        self.set_text_color(*BLACK)
        w_n = 7
        w_text = self.epw - indent - w_n
        for i, item in enumerate(items, 1):
            y0 = self.get_y()
            self.set_xy(self.l_margin + indent, y0)
            self.cell(w_n, 5.5, f"{i}.")
            self.set_xy(self.l_margin + indent + w_n, y0)
            self.multi_cell(w_text, 5.5, item)
        self.ln(1)

    def table_row(self, col1, col2, fill=False):
        if fill:
            self.set_fill_color(*LIGHT)
        else:
            self.set_fill_color(255, 255, 255)
        w1 = 55
        w2 = self.epw - w1
        y0 = self.get_y()
        self.set_xy(self.l_margin, y0)
        self.set_font("Courier", "", 9)
        self.set_text_color(*BLUE)
        self.multi_cell(w1, 5.5, col1, fill=True, border=0, new_x=XPos.RIGHT, new_y=YPos.TOP)
        h_col1 = self.get_y() - y0
        self.set_xy(self.l_margin + w1, y0)
        self.set_font("Arial", "", 9.5)
        self.set_text_color(*BLACK)
        self.multi_cell(w2, 5.5, col2, fill=fill, border=0)
        h_col2 = self.get_y() - y0
        self.set_y(y0 + max(h_col1, h_col2))

    def tabel_header(self, c1, c2):
        self.set_fill_color(*BLUE)
        self.set_text_color(255, 255, 255)
        self.set_font("Arial", "B", 9.5)
        w1 = 55
        w2 = self.epw - w1
        self.cell(w1, 6, c1, fill=True)
        self.cell(w2, 6, c2, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*BLACK)


d = Doc()
d.title_page()
d.add_page()

# -- 1 Indledning ------------------------------------------------------------
d.h1("1  Indledning")
d.body(
    "Terraen_model er en QGIS Processing-model der automatisk opbygger en "
    "hydrologisk konditioneret terrænmodel til brug i vådområdemodellering. "
    "Modellen henter det danske højdepunktsnet (DHM Terræn) direkte fra "
    "Kortforsyningen, brænder drænlinjer (DHMLinje) ind i rastermodellen for "
    "at sikre korrekte afstrømningsveje, og udleder dernæst et kanalsystem til "
    "brug i videre oplandsbereegninger."
)
d.body(
    "Resultatet er tre outputfiler der gemmes automatisk i projektmappens "
    "Outputfiler_<omraade>/-undermappe:"
)
d.bullet([
    "Hoejdemodel.sdat  -  Den konditionerede terrænmodel (SAGA-format)",
    "Channel_Networks.gpkg  -  Udledt kanalsystem",
    "DHMLinje_Klippet.gpkg  -  Klippede DHMLinje-linjer (én pr. parallel gruppe)",
])
d.body("Overordnet arbejdsgang:")
d.numbered([
    "Download DHM Terræn for projektområdet (inkl. 3 km buffer)",
    "Hent og klip DHMLinje til samme buffer",
    "Udvælg geometrisk centrale linjer der, hvor parallelle linjer overlapper",
    "Drappe DHM's z-værdier på de udvalgte linjer",
    "Beregn kanalbundshøjder (z_interp) langs linjerne",
    "Brænd en V-formet dale ind i DHM med kosinusbaseret overgangszone (0-30 m)",
    "Erstat kanalbunden direkte (4 m zone) med z_interp-værdierne",
    "Fyld kunstige sænkninger (Fill Sinks)",
    "Udled kanalsystem og drænoplande",
    "Gem output",
])

# -- 2 Inputparametre --------------------------------------------------------
d.h1("2  Inputparametre")
d.tabel_header("Parameter", "Beskrivelse")
d.table_row("projektomraade",
            "Et polygon-lag der definerer projektområdets udstrækning. Modellen "
            "opretter en 3 km buffer og begrænser alle beregninger til dette udsnit.", fill=True)
d.table_row("token",
            "Adgangstoken til Kortforsyningen (Geodatastyrelsen). Bruges til at "
            "hente DHM Terræn-data via WCS.")
d.ln(3)
d.body(
    "DHMLinje hentes automatisk fra mappehierarkiet og kræver ingen manuel "
    "parameterindstilling (se afsnit 4.2)."
)

# -- 3 Modelkæden ------------------------------------------------------------
d.h1("3  Modelkæden - trin for trin")

steps = [
    (
        "Trin 1 - Buffer (6000 m)",
        [("Algoritme:", "native:buffer"), ("Input:", "projektomraade"), ("Distance:", "6000 m")],
        "Projektområdet udvides med 6 km i alle retninger. Den store buffer sikrer, at "
        "DHM-downloaden og de efterfølgende hydrologiske beregninger ikke rammes af "
        "kanteffekter. Fill-Sinks-algoritmen er især følsom over for kortede "
        "terrænmodeller, idet kunstige vægge langs rasterets kant kan tvinge "
        "afstrømning i forkerte retninger. En bred buffer er særligt vigtig for "
        "store, flade vådområder med lange afstrømningsveje.",
        None
    ),
    (
        "Trin 2 - Hent DHM Terræn (WCS)",
        [("Algoritme:", "script:hent_dhm_wcs"),
         ("Service:", "api.dataforsyningen.dk/dhm_wcs_DAF"),
         ("Coverage:", "dhm_terraen"),
         ("Opløsning:", "2 m/pixel (standard)"),
         ("Projektion:", "EPSG:25832 (UTM zone 32N)")],
        "DHM Terræn er det danske LiDAR-baserede digitale terrænmodel med en original "
        "opløsning på ca. 0,4 m/pixel. Modellen downloader det relevante udsnit svarende "
        "til 3 km-bufferen. Opløsningen er som standard sat til 2 m/pixel for at balancere "
        "detaljegrad og procestid; den kan nedjusteres til 0,4 m for høj præcision eller "
        "op til 10 m for oversigtsformål.\n"
        "GDAL's WCS-driver bruges til at åbne servicen via en XML-konfiguration gemt i "
        "GDAL's virtuelle filsystem (/vsimem/). Derefter udklippes og resamples det ønskede "
        "udsnit med gdal.Translate.",
        None
    ),
    (
        "Trin 3 - Indput DHMLinje (automatisk)",
        [("Algoritme:", "script:indput_dhmlinje"),
         ("Kilde:", "Grunddata/DHMLinje/DHMLinje.shp")],
        "DHMLinje er et linjekortlag der repræsenterer vandløb og drænkanaler. "
        "I stedet for at brugeren manuelt skal pege på filen, finder scriptet automatisk "
        "DHMLinje.shp ved at søge opad og nedad i mappehierarkiet fra enten en "
        "brugerkonfigureret rodmappe (QgsSettings) eller QGIS-projektets hjemmemappe. "
        "Se afsnit 4.2 for detaljer.",
        None
    ),
    (
        "Trin 4 - Klip DHMLinje til buffer",
        [("Algoritme:", "native:clip"),
         ("Input:", "DHMLinje (trin 3)"),
         ("Overlay:", "3000 m buffer (trin 1)")],
        "Begrænser DHMLinje-laget til det relevante undersøgelsesområde og reducerer "
        "efterfølgende procestid i trin 5-14.",
        None
    ),
    (
        "Trin 5 - Udvælg midterste linjer",
        [("Algoritme:", "script:udpeg_midterste_linjer"),
         ("Søgeafstand:", "8 m")],
        "DHMLinje-datasættet indeholder mange steder to eller flere parallelle linjer der "
        "repræsenterer det samme vandløb. Hvis alle linjer bruges i den efterfølgende "
        "V-profil-korrektion, opstår der sammenstødende bufferzoner og inkonsistente "
        "z-værdier.\n"
        "Scriptet grupperer linjer der ligger inden for 8 m af hinanden (buffer + dissolve "
        "+ singleparts) og beholder for hver gruppe kun den linje, hvis centroid er nærmest "
        "gruppe-centroiden. Se afsnit 4.3 for detaljer.",
        None
    ),
    (
        "Trin 6 - Drappe Z fra DHM",
        [("Algoritme:", "native:setzfromraster"),
         ("Input:", "Midterste linjer (trin 5)"),
         ("Raster:", "DHM Terræn (trin 2)"),
         ("Band:", "1"),
         ("NODATA:", "-9999")],
        "Tildeler en z-koordinat til hvert knudepunkt på de udvalgte linjer ved at slå "
        "DHM-højden op i den tilsvarende rastercelle. Outputlaget er 3D-linjer, hvor hvert "
        "vertex har en z-værdi der afspejler det aktuelle terræn.\n"
        "Drapping-trinnet er nødvendigt fordi native:pointsalonglines (trin 8) udlæser "
        "z-koordinaten fra linjernes geometri som $z-attributten. Uden dette trin ville "
        "alle z-værdier være 0 eller udefinerede, og z_interp-beregningen i trin 9 ville "
        "give NULL for alle punkter.",
        None
    ),
    (
        "Trin 7 - Beregn linjelængde",
        [("Algoritme:", "native:fieldcalculator"),
         ("Nyt felt:", "line_length"),
         ("Udtryk:", "$length")],
        "Tilføjer linjelængden som et attributfelt til hver DHMLinje-feature. "
        "Feltet bruges i trin 8 til at generere samplingpunkter med korrekt afstandsangivelse.",
        None
    ),
    (
        "Trin 8 - Punkter langs linjer (2 m)",
        [("Algoritme:", "native:pointsalonglines"),
         ("Interval:", "2 m")],
        "Genererer samplingpunkter langs hver DHMLinje-feature med 2 m mellemrum. "
        "Hvert punkt arver linjens 3D-geometri fra trin 6, og z-koordinaten er "
        "tilgængelig via QGIS-udtrykket $z. Algoritmen tilføjer desuden attributten "
        "distance (afstand fra linjens startpunkt).",
        None
    ),
    (
        "Trin 9 - Beregn z_interp",
        [("Algoritme:", "native:fieldcalculator"),
         ("Nyt felt:", "z_interp"),
         ("Udtryk:", '"START_Z" + ("END_Z"-"START_Z") * ("distance"/"line_length")')],
        "Forsøger at beregne en lineær kanalbundshøjde interpoleret fra linjens "
        "startpunkt (START_Z) til slutpunkt (END_Z) proportionalt med afstanden. "
        "Hensigten er at bruge DHM-terrænet i endepunkterne som reference frem for "
        "de rå, potentielt støjbehæftede punkthøjder langs linjen.\n\n"
        "Praktisk begrænsning: START_Z og END_Z er ikke standardattributter fra "
        "native:pointsalonglines. Feltberegneren returnerer NULL for alle "
        "samplingpunkter, og z_interp bidrager ikke i praksis til den endelige "
        "terrænmodel. Terrænkonditioneringen baseres udelukkende på V-profil-"
        "sænkningen fra trin 14. Se afsnit 7 (kendte begrænsninger).",
        '"START_Z" + ("END_Z" - "START_Z") * ("distance" / "line_length")'
    ),
    (
        "Trin 10 - Buffer (4 m) om z_interp-punkter",
        [("Algoritme:", "native:buffer"),
         ("Distance:", "4 m")],
        "Opbygger en 4 m bred zone langs kanalen. Inden for denne zone erstattes DHM "
        "direkte med z_interp-værdierne (trin 11 og 15). Zone-bredden svarer til "
        "kanalbundens umiddelbare omgivelser og sikrer, at den brændte kanal er "
        "geometrisk sammenhængende i det endelige raster.",
        None
    ),
    (
        "Trin 11 - Rasterize z_interp",
        [("Algoritme:", "gdal:rasterize"),
         ("Burn felt:", "z_interp"),
         ("NODATA:", "-9999")],
        "Konverterer z_interp-punkternes 4 m bufferzoner til et raster med samme "
        "opløsning og udstrækning som DHM. Celler inden for bufferzonerne får "
        "z_interp-værdien; alle øvrige celler sættes til NODATA (-9999).",
        None
    ),
    (
        "Trin 12 - Rasterize DHMLinje (binær)",
        [("Algoritme:", "gdal:rasterize"),
         ("BURN:", "1"),
         ("INIT:", "0")],
        "Opretter et binært raster (0/1) der angiver, hvilke celler der ligger direkte "
        "på en DHMLinje. Outputtet bruges i trin 13 som kilde til afstandsberegningen.",
        None
    ),
    (
        "Trin 13 - Nærhedsraster (proximity)",
        [("Algoritme:", "gdal:proximity"),
         ("MAX_DISTANCE:", "25 m"),
         ("NODATA:", "26")],
        "Beregner for hver rastercelle den korteste afstand til nærmeste DHMLinje-celle. "
        "Celler mere end 25 m væk tildeles NODATA-værdien 26, hvilket sikrer, at "
        "V-profilkorrektionen ikke påvirker terrænnet uden for en 25 m-zone. "
        "Zonebredden er afstemt med den hydrologiske bremseeffekt: kanalen giver kun "
        "meningsfuld drænpåvirkning inden for ca. 25 m fra vandløbslinjen.",
        None
    ),
    (
        "Trin 14 - Rasterberegner: kosinusbaseret V-profil",
        [("Algoritme:", "gdal:rastercalculator"),
         ("Raster A:", "DHM (trin 2)"),
         ("Raster B:", "Nærhedsraster (trin 13)")],
        "Kernen i terrænkonditioneringen. Skaber en blød V-formet sænkning i DHM "
        "centreret om DHMLinje:\n"
        "  - Maksimal korrektion ved afstand = 0:  1+cos(0) = 2 m sænkning\n"
        "  - Nul-korrektion ved afstand = 25 m:  1+cos(pi) = 0 m ændring\n"
        "  - Glat overgang: kosinusfunktionen giver S-kurveformet aftrapning\n"
        "  - numpy.maximum(0, ...) sikrer at formlen aldrig hæver terrænet",
        "A - numpy.maximum(0.0,\n"
        "      1.0 + numpy.cos(numpy.pi * numpy.minimum(B, 25.0) / 25.0))"
    ),
    (
        "Trin 15 - Merge: kombiner V-profil og z_interp",
        [("Algoritme:", "gdal:merge"),
         ("Lag (rækkefølge):", "[V-profil, z_interp]"),
         ("NODATA_INPUT:", "-9999")],
        "z_interp-rasteret (trin 11) har NODATA for alle celler uden for 4 m-bufferen, "
        "og V-profil-rasteret (trin 14) dækker hele fladen. Ved at liste z_interp-rasteret "
        "sidst i stakken overskriver dets gyldige celler V-profil-værdierne i kanalbunden, "
        "mens V-profil-rasteret udfylder resten.\n"
        "Endeligt resultat: uændret > 30 m / blød V-profil 4-30 m / z_interp 0-4 m.",
        None
    ),
    (
        "Trin 16 - Fill Sinks (Wang & Liu)",
        [("Algoritme:", "native:fillsinkswangliu"),
         ("Input:", "Merge-raster (trin 15)"),
         ("MIN_SLOPE:", "0,01")],
        "SAGA's Wang & Liu-algoritme fjerner kunstige sænkninger der hindrer sammenhængende "
        "afstrømningsveje. Et minimumsgradient på 0,01 sikrer at alle overflader har en "
        "svag hældning og derved afvander til en kant.\n"
        "Fill Sinks er nødvendigt selv efter V-profilkorrektionen fordi lokale LiDAR-artefakter "
        "(broer, bygninger) kan skabe lukkede sænkninger, og DHM-rasterets kantceller skal "
        "have klare afstrømningsveje.",
        None
    ),
    (
        "Trin 17 - Kanalsystem og drænoplande",
        [("Algoritme:", "sagang:channelnetworkanddrainagebasins"),
         ("THRESHOLD:", "5")],
        "SAGA's Channel Network and Drainage Basins-modul beregner akkumuleret "
        "afstrømningsareal og udleder kanaler der, hvor akkumuleret afstrømning "
        "overstiger tærskelværdien 5. Tærskelværdien styrer kanalnetværkets densitet: "
        "lavere værdier giver tættere net, højere værdier giver grovere net.",
        None
    ),
    (
        "Trin 18 - Gem output",
        [("Algoritme:", "script:terraen_model_output")],
        "Gemmer de tre resultater til projektmappens Outputfiler_<omraade>/-undermappe:\n"
        "  - Hoejdemodel.sdat  -  Det konditionerede raster (SAGA-format)\n"
        "  - Channel_Networks.gpkg  -  Kanalsystemet som GeoPackage\n"
        "  - DHMLinje_Klippet.gpkg  -  Udvalgte DHMLinje-linjer med drappede z-værdier\n"
        "Scriptet håndterer Windows-fillåse på GPKG-filer med fjernelse af lag, "
        "garbage collection og OGR DeleteDataSource. Se afsnit 4.4 for detaljer.",
        None
    ),
]

for title, kvs, text, code_txt in steps:
    d.h2(title)
    for k, v in kvs:
        d.kv(k, v)
    d.ln(2)
    d.body(text)
    if code_txt:
        d.code(code_txt)

# -- 4 Scripts ---------------------------------------------------------------
d.h1("4  Hjælpescripts")
d.body(
    "Alle scripts ligger i mappen Scripts/ og registreres automatisk i QGIS "
    "Processing ved opstart via modelmappe_interface.py."
)

d.h2("4.1  hent_dhm_wcs_script.py")
d.body("Downloader DHM Terræn fra Kortforsyningen via GDAL's WCS-driver.")
d.bullet([
    "Bygger en WCS-XML-konfiguration i GDAL's virtuelle filsystem (/vsimem/dhm_wcs.xml)",
    "Åbner servicen som et virtuelt GDAL-datasæt",
    "Udklippes og resamples det ønskede udsnit med gdal.Translate",
    "Understøtter opløsninger fra 0,4 m (original LiDAR) til 10 m",
])
d.body(
    "Hvorfor scriptet er nødvendigt: QGIS's standardalgoritmer kan hente WMS/WFS, men "
    "ikke WCS i rasterformat med brugerdefineret opløsning og automatisk klipning til "
    "et bbox - det kræver GDAL-API direkte."
)

d.h2("4.2  Indput_DHMLinje.py")
d.body("Finder og returnerer stien til DHMLinje.shp automatisk uden brugerinput.")
d.body("Søgelogikken er trelaget:")
d.numbered([
    "Tjekker brugerkonfigureret rodmappe fra QgsSettings "
    "(vaadomraade_modeller/mappe), inkl. rekursiv søgning nedad i op til 4 niveauer",
    "Søger opad fra QGIS-projektets hjemmemappe i op til 6 niveauer",
    "Kaster QgsProcessingException med vejledende besked hvis ingen søgning lykkes",
])
d.body(
    "Hvorfor scriptet er nødvendigt: En hardkodet sti ville kræve manuel tilpasning "
    "ved hvert projekt. Søgelogikken gør modellen portabel på tværs af maskiner og "
    "projektmappestrukturer, blot Grunddata/DHMLinje/ er til stede et sted i hierarkiet."
)

d.h2("4.3  Udpeg_Midterste_Linjer.py")
d.body("Udvælger én repræsentativ linje per gruppe af parallelle linjer.")
d.body("Algoritme:")
d.numbered([
    "Buffer på distance meter (standard: 8 m) om alle inputlinjer + dissolve "
    "til ét multi-polygon-objekt",
    "native:multiparttosingleparts splitter multi-polygon'en i individuelle polygoner "
    "- én per sammenhængende klynge af parallelle linjer",
    "For hver gruppe-polygon beregnes dens centroid",
    "Alle inputlinjer der overlapper polygonen evalueres; den hvis centroid er "
    "nærmest polygon-centroiden markeres som vinder",
    "Kun vinderlinjerne skrives til outputlaget",
])
d.bullet([
    "n ulige: den geometrisk midterste linje vælges",
    "n lige: den linje der er nærmest midtpunktet vælges",
    "n = 1: linjen passerer uændret igennem",
])
d.body(
    "Hvorfor multiparttosingleparts er nødvendigt: native:buffer med DISSOLVE: True "
    "returnerer et enkelt multi-polygon-objekt når blot to buffere overlapper. Uden "
    "opsplitning behandles hele DHMLinje-netværket som én gruppe, og kun én enkelt "
    "linje overlever filtreringen.\n"
    "Hvorfor scriptet er nødvendigt: DHMLinje indeholder hyppigt to (eller flere) "
    "parallelle digitaliserede linjer for samme vandløb. Bruges alle linjer, opstår "
    "der overlappende V-profil-zoner der skaber uregelmæssige fordybninger og rygge "
    "i det konditionerede raster."
)

d.h2("4.4  Terraen_model_output.py")
d.body("Gemmer modelresultaterne til disk og håndterer Windows-specifikke fillåseproblemer.")
d.bullet([
    "Finder output-mappen via QgsSettings: <rod>/<projekt>/<omraade>/Outputfiler_<omraade>/",
    "Konverterer rasteret til SAGA-format (.sdat) med gdal:translate",
    "Gemmer DHMLinje_Klippet.gpkg med QgsVectorFileWriter.writeAsVectorFormatV3",
    "Håndterer Windows-fillåse: fjernelse af lag, gc.collect(), OGR DeleteDataSource, retry-løkke",
])

d.h2("4.5  Udtraek_Koordinater_Script.py")
d.body(
    "Udtrækker koordinaterne for det punkt der har det laveste og det højeste "
    "SEGMENT_ID i et inputlag. Bruges i Udpeg_Oplande_Model til at identificere "
    "start- og slutpunkterne af en beregnet afstrømningsrute."
)

# -- 5 Outputfiler -----------------------------------------------------------
d.h1("5  Outputfiler")
d.tabel_header("Fil", "Indhold")
d.table_row("Hoejdemodel.sdat",
            "Konditioneret og sink-fyldt terrænmodel i SAGA-format. Bruges direkte i "
            "SAGA-baserede hydrologimodeller og kan indlæses i QGIS som et rasterbånd.", fill=True)
d.table_row("Channel_Networks.gpkg",
            "Udledt kanalsystem som vektorlinjer. Inkluderer SAGA's standardattributter: "
            "flow accumulation, kanalbrede-estimat m.fl.")
d.table_row("DHMLinje_Klippet.gpkg",
            "De udvalgte DHMLinje-linjer klippet til 3 km-bufferen (én pr. parallel gruppe), "
            "med drappede z-værdier fra DHM Terræn. Bruges til visuel kontrol og kan danne "
            "grundlag for videre hydraulisk modellering.", fill=True)
d.ln(3)

# -- 6 Forudsætninger --------------------------------------------------------
d.h1("6  Forudsætninger og opsætning")
d.numbered([
    "Projektmappestruktur: Interfacet (modelmappe_interface.py) skal have registreret "
    "rodmappen under QgsSettings-nøglene vaadomraade_modeller/mappe, /projektnavn og "
    "/projektomraade_navn.",
    "Grunddata: Mappen Grunddata/DHMLinje/DHMLinje.shp skal være tilgængelig i "
    "hierarkiet under rodmappen.",
    "Kortforsyningen-token: En gyldig API-token er påkrævet for at downloade DHM "
    "Terræn. Tokens udstedes gratis på dataforsyningen.dk.",
    "QGIS-plugins: SAGA-algoritmer kræver SAGA installeret og registreret i QGIS "
    "Processing (Processing -> Options -> Providers -> SAGA).",
    "QGIS-projektet skal være gemt (Navngiv projekt i interfacet), og Tilføj "
    "projektområde skal have oprettet outputmappen inden Terraen_model køres.",
])

# -- 7 Kendte begrænsninger --------------------------------------------------
d.h1("7  Kendte begrænsninger")
d.bullet([
    "z_interp virker ikke uden START_Z/END_Z-felter: Den lineære interpolationsformel "
    "i trin 9 forudsætter at inputlaget har attributterne START_Z og END_Z. Da "
    "native:pointsalonglines ikke tilføjer disse felter, er z_interp NULL i praksis. "
    "Trin 11 (rasterize z_interp) producerer derved et rent NODATA-raster, og "
    "merge-trinnet (trin 15) baseres udelukkende på V-profil-sænkningen. "
    "For fuld funktionalitet bør formlen erstattes med $z (Z fra punktgeometrien).",
    "Afbrudte linjesegmenter: Hvis DHMLinje-segmenter ikke er forbundne ende til ende, "
    "kan kanalsystemet (trin 17) vælge afstrømningsveje der omgår de brændte kanaler. "
    "Løsningen er at sikre geometrisk sammenhæng i inputdatasættet.",
    "Z-værdier fra DHM: z_interp afspejler den faktiske DHM-højde i hvert samplingpunkt. "
    "Ved linjer der krydser vejdæmninger eller broer kan z-værdien være forhøjet relativt "
    "til den egentlige kanalbund, da DHM inkluderer befæstede overflader. Korrekt "
    "håndtering kræver supplerende data (fx ledningsregistre).",
    "Netværksforbindelse: Trin 2 kræver adgang til api.dataforsyningen.dk port 443. "
    "I netværksmiljøer med proxy eller VPN skal GDAL/QGIS konfigureres med korrekte "
    "proxyindstillinger.",
    "DHM-opløsning: Standard 2 m er passende for vådområdemodellering i hektar-skala. "
    "For meget store projektområder (> 50 km2) anbefales 5-10 m for at reducere procestiden.",
])

d.output(OUT)
print(f"PDF skrevet: {OUT}")
