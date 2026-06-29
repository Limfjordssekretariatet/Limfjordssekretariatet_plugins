"""
Ugentlig download af EJF 'Ejerskab med stamoplysninger' fra Datafordeleren SFTP.

Konfigurer de fire variabler i KONFIGURATION-sektionen nedenfor,
kør derefter opret_opgave.ps1 én gang for at sætte den ugentlige opgave op.
"""

import os
import sys
import logging
from datetime import datetime
from pathlib import Path

# -----------------------------------------------------------------------
# KONFIGURATION — tilpas disse fire variabler
# -----------------------------------------------------------------------

SFTP_BRUGER    = 'din_tjenestebruger'       # brugernavn på Datafordeleren
SFTP_ADGANGSKODE = 'din_adgangskode'        # adgangskode
SFTP_FIL       = '/ejf/ejerskab.json'       # sti til filen på FTP-serveren
                                             # (se under dit abonnement på Datafordeleren)

# -----------------------------------------------------------------------
# Lokalt gem-sted — filen lægges i plugin-mappen automatisk
# -----------------------------------------------------------------------

PLUGIN_DIR   = Path(__file__).parent
LOKAL_FIL    = PLUGIN_DIR / 'data' / 'ejf' / 'ejerskab_stamoplysninger.json'
LOG_FIL      = PLUGIN_DIR / 'data' / 'ejf' / 'download.log'
SFTP_HOST    = 'ftp2.datafordeler.dk'
SFTP_PORT    = 22

# -----------------------------------------------------------------------

logging.basicConfig(
    filename=str(LOG_FIL),
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)


def main():
    LOKAL_FIL.parent.mkdir(parents=True, exist_ok=True)
    logging.info('Download startet')

    try:
        import paramiko
    except ImportError:
        logging.error('paramiko ikke installeret — kør: pip install paramiko')
        sys.exit('FEJL: pip install paramiko')

    try:
        transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
        transport.connect(username=SFTP_BRUGER, password=SFTP_ADGANGSKODE)
        sftp = paramiko.SFTPClient.from_transport(transport)

        # Download til midlertidig fil — overskriv kun ved succes
        tmp = LOKAL_FIL.with_suffix('.tmp')
        sftp.get(SFTP_FIL, str(tmp))
        sftp.close()
        transport.close()

        tmp.replace(LOKAL_FIL)
        størrelse_mb = LOKAL_FIL.stat().st_size / 1_048_576
        logging.info(f'Gemt: {LOKAL_FIL} ({størrelse_mb:.1f} MB)')
        print(f'OK: {LOKAL_FIL} ({størrelse_mb:.1f} MB)')

    except Exception as e:
        logging.error(f'Download fejlede: {e}')
        sys.exit(f'FEJL: {e}')


if __name__ == '__main__':
    main()
