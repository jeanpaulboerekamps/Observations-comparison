# Waarnemingen Gelijkeniszoeker 0.2

Een Streamlit-prototype dat binnen een gekozen gebied nog niet tot soort
geïdentificeerde iNaturalist-waarnemingen vergelijkt met visueel vergelijkbare
waarnemingen uit hetzelfde gebied of een zone daaromheen.

De toepassing **identificeert geen soorten**. Zij rangschikt alleen andere
waarnemingen op overeenkomst tussen hun foto's.

## Werkwijze

1. Open een bestaand GeoJSON-gebied of teken een gebied op de kaart.
2. Zoek en selecteer verplicht een taxonomische orde.
3. Kies de zoekafstand rond het gebied: 0 km (standaard), 100 km of 1000 km.
4. Kies een periode en een begrenzing voor de vergelijkingsset.
5. Start de vergelijking en selecteer daarna een waarneming.

De app selecteert niet automatisch een inhoudelijk belangrijke waarneming. Na
het verzamelen van de zoekset blijft de keuze leeg totdat de gebruiker bewust
een bronwaarneming selecteert. Mogelijke overeenkomsten worden standaard pas
getoond vanaf overeenkomstsscore 80; deze grens kan worden aangepast.

De waarnemingen die onderzocht worden:

- liggen exact binnen het gekozen gebied;
- hebben ten minste één foto;
- behoren volgens hun huidige iNaturalist-identificatie tot de gekozen orde;
- zijn nog niet op soortniveau of lager geïdentificeerd.

Volledig onbekende waarnemingen vallen buiten de selectie, omdat hun orde nog
niet bekend is. Identificaties op bijvoorbeeld orde-, familie-, tribus- of
genusniveau tellen wel mee.

## Gebied en zoekafstand

De gebiedsfunctionaliteit is afkomstig uit Biodiversiteit Verkenner. Gebieden
kunnen als GeoJSON worden geopend, getekend, via de URL worden hersteld en weer
worden gedownload.

- **0 km:** alleen het gekozen gebied;
- **100 km:** het gebied plus een buffer van 100 km vanaf de buitengrens;
- **1000 km:** het gebied plus een buffer van 1000 km vanaf de buitengrens.

De buffer wordt in meters berekend in een lokale azimutale projectie en daarna
teruggezet naar WGS84. De iNaturalist-aanvraag gebruikt eerst de begrenzende
rechthoek; daarna voert de app lokaal de exacte polygooncontrole uit.

## Visuele vergelijking

Per waarneming worden maximaal twee foto's verwerkt. Een vooraf getrainde
EfficientNet-B0 zet de foto's om in beeldvectoren. Bij meerdere foto's wordt
het gemiddelde van de genormaliseerde vectoren gebruikt. De kandidaten worden
gerangschikt met cosinusovereenkomst.

De getoonde overeenkomstsscore loopt van 0 tot 100, maar is geen
waarschijnlijkheidspercentage en geen taxonomische identificatie. Een score 80
betekent dus niet dat er 80% kans is op dezelfde soort. Achtergrond, camerahoek,
levensstadium en fotokwaliteit kunnen de rangschikking beïnvloeden.

## Beperking van belasting

- Er wordt pas gezocht nadat zowel een gebied als een orde is gekozen.
- API-verzoeken worden begrensd tot ongeveer één per seconde.
- De vergelijkingsset is expliciet begrensd op 100, 250 of 500 waarnemingen.
- De nieuwste passende waarnemingen worden gebruikt wanneer er meer resultaten
  zijn dan de ingestelde grens.
- API-antwoorden en berekende beeldvectoren worden tijdelijk gecachet.

## Installatie

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Bij de eerste beeldvergelijking downloadt torchvision eenmalig de vooraf
getrainde EfficientNet-B0-gewichten. Hiervoor zijn geen API-sleutels of
Streamlit Secrets nodig. De afhankelijkheden gebruiken expliciet de
CPU-uitvoering van PyTorch; er worden geen CUDA- of andere GPU-pakketten
geïnstalleerd.

## Privacy en auteursrecht

De app gebruikt openbare iNaturalist-gegevens. Foto's worden opgehaald om
tijdelijke beeldvectoren te berekenen; de afbeeldingsbestanden worden niet als
onderdeel van de toepassing opgeslagen. Bij de resultaten worden beschikbare
fotocredits en licentiecodes getoond. Controleer voor verder gebruik altijd de
licentie op de gekoppelde iNaturalist-pagina.
