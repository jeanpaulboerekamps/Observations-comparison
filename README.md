# Waarnemingen Gelijkeniszoeker 0.4

De app vergelijkt foto's van iNaturalist-waarnemingen die wel tot een gekozen orde, maar nog niet tot een soort zijn geïdentificeerd. De vergelijking gebruikt vooraf opgeslagen beeldkenmerken. Een score van 80 of 90 is een modelschaal, geen kans op dezelfde soort.

## Instellen

1. Open in het gezonde Supabase-project **SQL Editor → New query**. Plak de inhoud van supabase/schema.sql en klik **Run**. Dit maakt de vector-extensie, tabellen en leesrechten aan.
2. Upload de bestanden uit deze zip naar de hoofdmap van je bestaande GitHub-repository. Behoud de mappen supabase/ en .github/workflows/; app.py hoort in de hoofdmap.
3. Open **Supabase → Project Settings → API Keys** en gebruik de project-URL, de publishable key en de secret key.
4. Maak in **GitHub → Settings → Secrets and variables → Actions** twee repository secrets aan: SUPABASE_URL (project-URL) en SUPABASE_SECRET_KEY (de sb_secret_… sleutel). Zet de geheime sleutel nooit in GitHub-bestanden of in een chat.
5. Zet in **Streamlit → Manage app → Settings → Secrets** uitsluitend de project-URL en de publishable key:

    SUPABASE_URL = "https://jouw-project.supabase.co"
    SUPABASE_PUBLISHABLE_KEY = "sb_publishable_..."

De app meldt dat de index nog leeg is totdat er een indexeeractie klaar is.

## Eerste index

1. Sla een gebied als GeoJSON op en upload dit in je repository als areas/mijn-gebied.geojson. Het gehele gewenste zoekgebied, inclusief een buffer van 100 of 1000 km, moet binnen dat indexgebied vallen.
2. Open **GitHub → Actions → Build observation index → Run workflow**. Vul het GeoJSON-pad, het iNaturalist-taxon-ID van de orde, een naam en een begin- en einddatum in. Begin met een klein gebied en een beperkte periode.
3. Wacht tot de workflow groen is. Daarna kun je het begin- en zoekgebied binnen de geïndexeerde zone kiezen in Streamlit. Bij een fout krijgt de index geen status 'complete'; je kunt dezelfde workflow opnieuw starten.

De app meldt als de zone of periode niet volledig binnen één gereed indexgebied ligt. Boven 20.000 vergelijkingswaarnemingen vraagt hij om een kleiner gebied of kortere periode. De eerste 200 paren verschijnen op het scherm; de CSV bevat alle paren.

## Foto's en rechten

Supabase bewaart de publieke waarnemingsgegevens, de fotolink en het beeldkenmerk. Foto's worden tijdens het indexeren opgehaald om dat kenmerk te berekenen. Ze worden nog niet naar Supabase Storage gekopieerd: per foto moet eerst de licentie beoordeeld worden. Tijdens het vergelijken wordt de iNaturalist-API niet aangesproken; voor het tonen van een treffer laadt de browser de originele fotolink.

De gratis Supabase-laag heeft beperkte capaciteit en kan na inactiviteit pauzeren. Begin daarom klein.
