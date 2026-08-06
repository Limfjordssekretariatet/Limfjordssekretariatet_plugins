import time
import requests
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from qgis.core import QgsGeometry

# OAuth-token cache: {client_id: {'token': str, 'expires': float}}
_token_cache: dict[str, dict] = {}


class DatafordelerClient:
    """Geometri og ejeroplysninger via Datafordeleren (OAuth Shared Secret)."""

    WFS_URL = 'https://wfs.datafordeler.dk/MATRIKLEN2/MatGaeldendeOgForeloebigWFS/1.0.0/WFS'

    EJERFORHOLD = {
        '10': 'Privat eje',
        '20': 'Aktie-/anpartsselskab',
        '30': 'Interessentskab',
        '40': 'Forening/fond',
        '50': 'Kommunalt eje',
        '60': 'Statsligt eje',
        '70': 'Regionalt eje',
        '80': 'Alment boligselskab',
        '90': 'Anden offentlig ejer',
        '99': 'Ukendt',
    }

    TOKEN_URL   = 'https://auth.datafordeler.dk/realms/distribution/protocol/openid-connect/token'
    GRAPHQL_URL = 'https://graphql.datafordeler.dk/flexibleCurrent/v1/'

    def __init__(self, client_id: str, client_secret: str, wfs_apikey: str):
        self._client_id     = client_id
        self._client_secret = client_secret
        self._wfs_apikey    = wfs_apikey
        self.session = requests.Session()
        self.session.headers['User-Agent'] = (
            'Lodsejere QGIS Plugin / Limfjordssekretariatet'
        )

    # ------------------------------------------------------------------
    # Fejlhåndtering
    # ------------------------------------------------------------------

    @staticmethod
    def _tjek_svar(resp, hvad: str) -> None:
        """Rejs en fejl der siger HVORFOR, ikke bare hvilken statuskode.

        requests' raise_for_status() giver kun "403 Client Error: Forbidden
        for url: ...". Datafordeleren skriver som regel årsagen i svarets
        krop, og uden den står brugeren uden noget at handle på.
        """
        if resp.ok:
            return
        krop = ' '.join((resp.text or '').split())[:300]
        besked = f'{hvad} fejlede: HTTP {resp.status_code}'
        if krop:
            besked += f' — {krop}'
        if resp.status_code in (401, 403):
            besked += (
                '\n\nAdgangen blev afvist, selvom log ind lykkedes. Det '
                'betyder næsten altid, at tjenestebrugeren ikke har netop '
                'denne tjeneste tilknyttet. Tjek på datafordeler.dk under '
                'Selvbetjening → Brugerstyring, at brugeren har adgang til '
                'Ejerfortegnelsen (flexibleCurrent), og at abonnementet '
                'dækker de oplysninger der hentes — adgang til '
                'personoplysninger skal godkendes særskilt.'
            )
        raise RuntimeError(besked)

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        cached = _token_cache.get(self._client_id)
        if cached and cached['expires'] > time.time() + 30:
            return cached['token']

        resp = self.session.post(
            self.TOKEN_URL,
            data={
                'grant_type':    'client_credentials',
                'client_id':     self._client_id,
                'client_secret': self._client_secret,
            },
            timeout=15,
        )
        self._tjek_svar(resp, 'Log ind på Datafordeleren')
        data       = resp.json()
        token      = data['access_token']
        expires_in = int(data.get('expires_in', 3600))
        _token_cache[self._client_id] = {
            'token':   token,
            'expires': time.time() + expires_in,
        }
        return token

    # ------------------------------------------------------------------
    # Matrikler (WFS)
    # ------------------------------------------------------------------

    @staticmethod
    def _poslist_to_wkt_ring(poslist_text: str) -> str:
        coords = poslist_text.split()
        pairs  = [f'{coords[i]} {coords[i+1]}' for i in range(0, len(coords) - 1, 2)]
        return '(' + ', '.join(pairs) + ')'

    def _gml_surface_to_wkt(self, geometri_el) -> str:
        # Håndterer gml:Polygon og gml:MultiSurface/gml:Surface
        rings = []
        for poslist in geometri_el.iter('{http://www.opengis.net/gml/3.2}posList'):
            rings.append(self._poslist_to_wkt_ring(poslist.text.strip()))
        if not rings:
            return ''
        # Første ring = ydre, resten = huller
        return 'POLYGON(' + ', '.join(rings) + ')'

    NS = {
        'wfs': 'http://www.opengis.net/wfs/2.0',
        'mat': 'http://data.gov.dk/schemas/matrikel/1',
        'gml': 'http://www.opengis.net/gml/3.2',
    }

    def get_jordstykker(self, geometry) -> list[dict]:
        bb   = geometry.boundingBox()
        bbox = f'{bb.xMinimum()},{bb.yMinimum()},{bb.xMaximum()},{bb.yMaximum()},EPSG:25832'

        start_index = 0
        page_size   = 500
        all_features = []

        while True:
            resp = self.session.get(
                self.WFS_URL,
                params={
                    'SERVICE':    'WFS',
                    'REQUEST':    'GetFeature',
                    'VERSION':    '2.0.0',
                    'TYPENAMES':  'mat:Jordstykke_Gaeldende',
                    'BBOX':       bbox,
                    'COUNT':      page_size,
                    'STARTINDEX': start_index,
                    'apikey':     self._wfs_apikey,
                },
                timeout=60,
            )
            self._tjek_svar(resp, 'Opslag af matrikler (Matriklen WFS)')

            root    = ET.fromstring(resp.content)
            members = root.findall('wfs:member', self.NS)
            all_features.extend(members)

            matched   = int(root.get('numberMatched', '0') or '0')
            returned  = int(root.get('numberReturned', '0') or '0')
            start_index += returned
            if start_index >= matched or returned == 0:
                break

        results = []
        for member in all_features:
            js = member.find('mat:Jordstykke_Gaeldende', self.NS)
            if js is None:
                continue

            gml_geom = js.find('mat:geometri', self.NS)
            if gml_geom is None:
                continue

            def txt(tag):
                el = js.find(f'mat:{tag}', self.NS)
                return el.text if el is not None and el.text else ''

            geom_wkt = self._gml_surface_to_wkt(gml_geom)

            bfe_raw = txt('samletFastEjendomLokalId')
            bfe     = str(int(bfe_raw)) if bfe_raw else ''

            results.append({
                'geometri_wkt':   geom_wkt,
                'ejerlavskode':   int(txt('ejerlavskode') or 0),
                'ejerlavsnavn':   '',
                'matrikelnummer': txt('matrikelnummer'),
                'bfe_nummer':     bfe,
            })
        return results

    # ------------------------------------------------------------------
    # Ejeroplysninger — EJF GraphQL
    # ------------------------------------------------------------------

    def get_ejer(self, bfe_nummer: str, only_companies: bool = True) -> dict:
        if not bfe_nummer:
            return self._tomt_ejer()

        token = self._get_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type':  'application/json',
        }

        nu = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        query = """
        query {
            EJFCustom_EjerskabBegraenset(
                virkningstid: "%s"
                where: { bestemtFastEjendomBFENr: { eq: %s } }
            ) {
                nodes {
                    bestemtFastEjendomBFENr
                    ejerforholdskode
                    status
                    ejendeVirksomhedCVRNr_20_Virksomhed_CVRNummer_ref {
                        CVRNummer
                        id_CVR_Navn_CVREnhedsId_ref { vaerdi }
                    }
                    ejendePersonBegraenset {
                        navn { navn }
                        standardadresse
                        beskyttelser { beskyttelsestype }
                        status
                    }
                }
            }
        }
        """ % (nu, bfe_nummer)

        resp = self.session.post(
            self.GRAPHQL_URL,
            json={'query': query},
            headers=headers,
            timeout=15,
        )
        self._tjek_svar(resp, 'Opslag af ejeroplysninger (Ejerfortegnelsen)')
        data = resp.json()

        if 'errors' in data:
            raise RuntimeError(f'GraphQL fejl: {data["errors"][0].get("message", "")}')

        nodes = (data.get('data') or {}).get('EJFCustom_EjerskabBegraenset', {}).get('nodes', [])

        # Vælg den gældende post (filtrer historiske fra)
        gaeldende = [n for n in nodes if n.get('status') == 'gældende']
        if not gaeldende:
            return self._tomt_ejer()

        return self._parse_nodes(gaeldende, only_companies)

    def _parse_nodes(self, nodes: list, only_companies: bool) -> dict:
        # Alle gældende ejere (kan være flere ved sameje)
        ejerforhold = str(nodes[0].get('ejerforholdskode') or '')

        navne    = []
        adresser = []
        cvr_numre = []
        adrbeskyt = 'Nej'

        for node in nodes:
            virk = node.get('ejendeVirksomhedCVRNr_20_Virksomhed_CVRNummer_ref')
            if virk:
                cvr  = str(virk.get('CVRNummer', ''))
                navn = (virk.get('id_CVR_Navn_CVREnhedsId_ref') or {}).get('vaerdi', '')
                navne.append(navn)
                cvr_numre.append(cvr)
                continue

            person = node.get('ejendePersonBegraenset')
            if person:
                if only_companies:
                    navne.append('Privat ejer')
                    continue
                navn_obj = person.get('navn') or {}
                navn = navn_obj.get('navn', '') if isinstance(navn_obj, dict) else ''
                navne.append(navn)
                adr = person.get('standardadresse', '')
                if adr and adr not in adresser:
                    adresser.append(adr)
                # Adressebeskyttelse kun ved fuld beskyttelse
                beskyttelser = person.get('beskyttelser') or []
                if any(b.get('beskyttelsestype') == 'adressebeskyttelse' for b in beskyttelser):
                    adrbeskyt = 'Ja'

        antal = len(navne)
        ejernavn = ' / '.join(filter(None, navne))
        if antal > 1:
            ejernavn = f'Sameje ({antal}): {ejernavn}'

        return {
            'ejernavn':           ejernavn,
            'ejeradresse':        ', '.join(adresser),
            'ejerforhold':        ejerforhold,
            'ejerforhold_tekst':  self.EJERFORHOLD.get(ejerforhold, ejerforhold),
            'cvr_nummer':         ' / '.join(cvr_numre),
            'adressebeskyttelse': adrbeskyt,
        }

    def _tomt_ejer(self) -> dict:
        return {
            'ejernavn':           '',
            'ejeradresse':        '',
            'ejerforhold':        '',
            'ejerforhold_tekst':  '',
            'cvr_nummer':         '',
            'adressebeskyttelse': '',
        }
