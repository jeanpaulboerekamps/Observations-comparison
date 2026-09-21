# Waarnemingen Gelijkeniszoeker 0.5.2

De app vergelijkt foto's van iNaturalist-waarnemingen die wel tot een gekozen orde, maar nog niet tot een soort zijn geïdentificeerd. De vergelijking gebruikt vooraf opgeslagen beeldkenmerken. Een score van 80 of 90 is een modelschaal, geen kans op dezelfde soort.

## Instellen

1. Open in het gezonde Supabase-project **SQL Editor → New query**. Plak de inhoud van supabase/schema.sql en klik **Run**. Dit maakt de vector-extensie, tabellen en leesrechten aan.
2. Upload de bestanden uit deze zip naar de hoofdmap van je bestaande GitHub-repository. Behoud de mappen supabase/ en .github/workflows/; app.py hoort in de hoofdmap.
3. Open **Supabase → Project Settings → API Keys** en gebruik de project-URL, de publishable key en de secret key.
4. Maak in **GitHub → Settings → Secrets and variables → Actions** twee repository secrets aan: SUPABASE_URL (project-URL) en SUPABASE_SECRET_KEY (de sb_secret_… sleutel). Zet de geheime sleutel nooit in GitHub-bestanden of in een chat.
5. Zet in **Streamlit → Manage app → Settings → Secrets** de project-URL en de publishable key:

    SUPABASE_URL = "https://jouw-project.supabase.co"
    SUPABASE_PUBLISHABLE_KEY = "sb_publishable_..."

De app meldt dat de index nog leeg is totdat er een indexeeractie klaar is.

## Begeleide handmatige opmerkingen (geen iNaturalist API-toegang nodig)

Het reeds uitgevoerde `supabase/reviews.sql` hoeft niet opnieuw uitgevoerd te worden. Je hoeft voor de eerste proef alleen `app.py` en `review.py` uit deze versie naar de hoofdmap van dezelfde GitHub-repository te uploaden. Open de Streamlit-app opnieuw, kies je gebied en start de vergelijking.

Per paar kies je één van vier beoordelingen. Bij keuze 1, 2 of 3 toont de app **eerst één** Engelse tekst in een gewoon tekstvak en een knop naar de bijbehorende waarneming. Tik op een iPad in het tekstvak en kies **Selecteer alles → Kopieer**; de tekst loopt automatisch over meerdere regels. Plak de tekst op iNaturalist en ga terug naar het app-tabblad. Klik pas dan op **Ik heb de opmerking geplaatst**. Vervolgens verschijnt de tekst en link voor de tweede waarneming. Keuze 4 wordt direct afgerond, zonder opmerkingen. iNaturalist gebruikt het account waarmee je in je browser bent aangemeld; een link naar een waarneming kan niet zelf een ander account kiezen. Controleer je gebruikersnaam op de geopende iNaturalist-pagina voordat je plakt.

Zonder extra instelling bewaart de app deze voortgang alleen zolang het tabblad open is. Je kunt met **Alle paren en voortgang downloaden als CSV** een overzicht bewaren. Voor blijvende opslag in de al aangemaakte Supabase-tabel voeg je in **Streamlit → Manage app → Settings → Secrets** onder de bestaande twee Supabase-regels ook deze twee regels toe:

```toml
SUPABASE_SECRET_KEY = "sb_secret_..."
REVIEW_PASSPHRASE = "een-eigen-lange-geheime-toegangscode"
```

Kies zelf een unieke toegangscode van minimaal 16 tekens. Zet de secret key en toegangscode nooit in GitHub-bestanden of in de chat. Na het opslaan verschijnt in de app een veld waarmee alleen jij de beoordelingsknoppen kunt ontgrendelen. De database gebruikt voor de handmatige modus `reviewer_id = 0`; bij een latere OAuth-koppeling krijgt het echte iNaturalist-account zijn eigen ID. De knop **Ik heb de opmerking geplaatst** registreert jouw bevestiging; hij kan niet controleren of je echt op iNaturalist hebt gepubliceerd.

## Automatisch plaatsen in een toekomstige versie

Voor automatisch plaatsen is een eigen iNaturalist OAuth-applicatie nodig. iNaturalist staat het aanvragen daarvan op dit moment voor dit account niet toe. De OAuth-code blijft aanwezig voor het geval die toestemming later beschikbaar komt.

## Eerste index

1. Sla een gebied als GeoJSON op en upload dit in je repository als areas/mijn-gebied.geojson. Het gehele gewenste zoekgebied, inclusief een buffer van 100 of 1000 km, moet binnen dat indexgebied vallen.
2. Open **GitHub → Actions → Build observation index → Run workflow**. Vul het GeoJSON-pad, het iNaturalist-taxon-ID van de orde, een naam en een begin- en einddatum in. Begin met een klein gebied en een beperkte periode.
3. Wacht tot de workflow groen is. Daarna kun je het begin- en zoekgebied binnen de geïndexeerde zone kiezen in Streamlit. Bij een fout krijgt de index geen status 'complete'; je kunt dezelfde workflow opnieuw starten.

De app meldt als de zone of periode niet volledig binnen één gereed indexgebied ligt. Boven 20.000 vergelijkingswaarnemingen vraagt hij om een kleiner gebied of kortere periode. De paren zijn verdeeld over pagina's van tien; de CSV bevat ze allemaal.

## Foto's en rechten

Supabase bewaart de publieke waarnemingsgegevens, de fotolink en het beeldkenmerk. Foto's worden tijdens het indexeren opgehaald om dat kenmerk te berekenen. Ze worden nog niet naar Supabase Storage gekopieerd: per foto moet eerst de licentie beoordeeld worden. Tijdens het vergelijken wordt de iNaturalist-API niet aangesproken; voor het tonen van een treffer laadt de browser de originele fotolink.

De gratis Supabase-laag heeft beperkte capaciteit en kan na inactiviteit pauzeren. Begin daarom klein.
