# Adgang til Datafordeleren — opsætning til Lodsejerudtræk

Denne vejledning supplerer `Dokumentation_datafordeler.docx` med de dele der
manglede: hvad pluginnet konkret kalder, hvilke entiteter der skal søges om,
og hvor nøglerne skrives ind. Trin 1–7 følger samme nummerering som Word-
dokumentet.

## Hvad pluginnet skal bruge

Tre oplysninger, som indtastes i dialogen **Vandprojekter → Lodsejerudtræk**
under afsnittet *Adgang til Datafordeleren*:

| Felt i dialogen | Hvad det er | Hvor det oprettes |
|---|---|---|
| Matriklen API-nøgle | API-nøgle til WFS-tjenesten | Trin 6 |
| EJF Client ID | OAuth klient-id | Trin 7 |
| EJF Shared Secret | Tilhørende hemmelighed | Trin 7 |

De gemmes i QGIS' indstillinger pr. bruger (`lodsejere/wfs_apikey`,
`lodsejere/client_id`, `lodsejere/client_secret`) og skal altså sættes op på
hver maskine.

## Hvad pluginnet konkret kalder

Tag dette med, når der søges om dataadgang — så matcher ansøgningen det
værktøjet faktisk gør:

| Formål | Endpoint |
|---|---|
| Log ind (OAuth) | `auth.datafordeler.dk/realms/distribution/protocol/openid-connect/token`, `grant_type=client_credentials` |
| Matrikler | `wfs.datafordeler.dk/MATRIKLEN2/MatGaeldendeOgForeloebigWFS/1.0.0/WFS`, typen `mat:Jordstykke_Gaeldende` |
| Ejeroplysninger | `graphql.datafordeler.dk/flexibleCurrent/v1/` |

Bemærk at ejeroplysningerne hentes fra **GraphQL-tjenesten `flexibleCurrent`**
— ikke REST-udstillingen af EJF. Adgang til den ene giver ikke adgang til den
anden.

## Trin 3–4: Ansøg om dataadgang, og hvilke entiteter

Forespørgslen bruger entiteten **`EJFCustom_EjerskabBegraenset`** og henter
derfra:

- **Virksomhedsejere** — CVR-nummer og virksomhedsnavn.
- **Personejere** — navn, standardadresse og beskyttelser
  (`ejendePersonBegraenset`). Det er personoplysninger og kræver særskilt
  godkendelse; uden den fejler hele opslaget, ikke kun personerne.

Vælges kun virksomhedsdata i ansøgningen, kan pluginnet stadig bruges med
afkrydsningen *"Vis kun virksomhedsejere"* — private ejere vises da som
"Privat ejer".

Ansøgningen gælder **kun det miljø den er søgt til**. Produktion og test er
adskilt; en godkendelse i test giver ikke adgang i produktion.

## Trin 5: IP-adresser — den hyppigste årsag til 403

IP-listen skal dække **alle de maskiner der bruger pluginnet**, ikke kun den
der oprettede IT-systemet. Det er her fejlen typisk opstår, når en kollega
skal i gang:

- Kollegaen sidder på en anden lokation eller et andet netværk.
- Der arbejdes hjemmefra eller over VPN, så udgående IP er en anden.
- Organisationens udgående IP er ændret.

Symptomet er karakteristisk: **log ind virker, matriklerne kommer hjem, men
ejeropslaget svarer 403.** Token-endpointet er ikke IP-spærret, så
legitimationsoplysningerne ser rigtige ud — det er kun de beskyttede data der
afvises.

### Når en kollega i en anden organisation skal bruge pluginnet

En anden organisation har sit eget netværk og dermed sin egen udgående
IP-adresse. Den står ikke på jeres liste, og opslaget vil derfor svare 403 —
også selvom nøglerne er rigtige.

Der er to veje, og valget er ikke kun teknisk:

**Deling af jeres nøgler.** Tilføj den anden organisations udgående IP til
listen, og udlevér Client ID og Secret. Det virker med det samme, men
Datafordeleren ser jeres IT-system, ikke deres: opslagene sker på jeres
dataadgang og jeres hjemmel, og udtræk af personoplysninger foretaget af en
anden organisation registreres som jeres. Bed om det faste udgående IP eller
hele området — mange organisationer har flere, og nogle skifter.

**Egen adgang hos dem.** De opretter deres eget IT-system med egen
dataadgang, egen API-nøgle, eget OAuth-hemmelighed og egen IP-liste. Det
tager længere tid, fordi ansøgningen om EJF-persondata skal godkendes, men
hver organisation henter så på sin egen hjemmel, og der deles ingen
hemmeligheder. Har organisationen i forvejen adgang til Datafordeleren til
andre formål, mangler de kun EJF-delen.

## Trin 6: Opret API-nøgle

API-nøglen hører til IT-systemet og oprettes i Datafordeler Administration
under det system, der skal hente data. Et IT-system autentificeres enten med
**API-nøgle** eller med **OAuth** — til dette plugin bruges begge dele:
API-nøglen til Matriklen-WFS'en (sendes som parameteren `apikey` i URL'en) og
OAuth til ejeroplysningerne.

> **Udfyldes lokalt:** den præcise klikvej i portalen er ikke beskrevet her,
> fordi den ikke fremgår af det oprindelige dokument. Tilføj skærmbillede og
> menusti, næste gang en nøgle oprettes.

## Trin 7: OAuth Shared Secret

Ved oprettelsen dannes et **Client ID** og en **Secret**. Secret'en vises kun
ved oprettelsen — kopiér den med det samme.

**Shared Secret har en udløbsdato.** Den dag den udløber, holder
ejeropslagene op med at virke, og fejlen ligner til forveksling en manglende
adgang. Notér datoen, og forny i god tid.

## Fejlsøgning

Fra og med version 0.1.6 viser pluginnet Datafordelerens egen forklaring i
fejlbeskeden. Læs hvilket af de tre kald der fejler:

| Besked begynder med | Betyder |
|---|---|
| *Log ind på Datafordeleren fejlede* | Client ID eller Secret er forkert eller udløbet (trin 7) |
| *Opslag af matrikler (Matriklen WFS) fejlede* | API-nøglen mangler eller er forkert (trin 6) |
| *Opslag af ejeroplysninger (Ejerfortegnelsen) fejlede* | Log ind virkede — det er dataadgangen (trin 3–4) eller IP-listen (trin 5) |

Ved 401/403 på det sidste: tjek **IP-listen først**, hvis kollegaen bruger
samme nøgler som en der har fået det til at virke. Bruger vedkommende sine
egne nøgler, er det snarere dataadgangen der mangler godkendelse.
