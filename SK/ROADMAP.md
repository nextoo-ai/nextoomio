# nextoomio – roadmapa v0.0.1 až v0.0.5

Verzia dokumentu: **1.0**  
Dátum: **2026-11-13**  
Stav: **Plán; žiadna položka nie je týmto dokumentom označená za implementovanú alebo overenú.**  
Funkčné pravidlá: [SPECIFICATION.md](SPECIFICATION.md), verzia 1.6.

## 1. Cieľ a pravidlá plánovania

Do v0.0.5 sa má dodať celý dohodnutý rozsah: samostatný prehrávač, všetky požadované vstupy, vlastný multiroom, Music Assistant, HA Voice, API, MQTT, akcie, Matter, základné webUI, manuálne A/B aktualizácie a podpora Raspberry Pi 3+ s bežne rozšírenými HiFiBerry kartami.

- Verzie sú kumulatívne. Každá zachováva funkcie predchádzajúcich verzií a pridáva nižšie uvedené funkcie.
- Špecifikácia určuje správanie; táto roadmapa určuje dodanie. Oprava chyby patrí do najbližšej verzie, ale nenahrádza jej plánované funkcie.
- Dátumy vydaní sa neurčujú bez implementačných a hardvérových výsledkov. Poradie a podmienky vydania sú záväznou pracovnou osnovou.
- Bezpečnosť príslušného rozhrania sa dodáva spolu s ním. API, upload image alebo ovládanie skupiny sa nesprístupnia najprv bez ochrany s prísľubom doplnenia vo v0.0.5.
- Lokálne ovládanie, nastavenie a vlastný multiroom fungujú bez cloudu projektu. Výpadok internetu nesmie vyradiť prehrávanie dostupných lokálnych/NAS súborov, ovládanie ani vlastnú skupinu.
- Všetkých päť verzií používa výhradne manuálny upload image. Online aktualizácie a podpisovanie/overovanie podpisov image nie sú súčasťou tejto roadmapy.
- Ciele meraní sú uvedené v špecifikácii, kapitole 13.2. Nesplnený cieľ sa vykáže ako nesplnený; neoverená integrácia alebo karta sa neoznačí za podporovanú.
- Vo v0.0.5 nesmie zostať dohodnutá funkcia iba ako návrh, stub, skrytá voľba alebo položka backlogu. Pri technickej prekážke sa otvorene zaznamená nesplnená požiadavka a plán sa upraví; rozsah sa nesmie potichu zúžiť.

## 2. Prehľad verzií

| Verzia | Výsledok pre používateľa | Hlavné závislosti |
| --- | --- | --- |
| **v0.0.1** | Na jednom RPi prehrám lokálnu hudbu alebo rádio, ovládam ho cez web/API a manuálne aktualizujem image. | Yocto, jeden overený HiFiBerry výstup, spoločná audio pipeline a správca relácií. |
| **v0.0.2** | Prehrám hudbu z NAS a vytvorím vlastnú synchronizovanú skupinu s referenciou hlasitosti pre každý satelit. | Funkčný základ v0.0.1, distribúcia audia, časová synchronizácia a riadenie skupiny. |
| **v0.0.3** | Pošlem zvuk cez AirPlay 2, Spotify Connect, Bluetooth alebo Music Assistant; vstupy a skupiny sa korektne preberajú. | Jednotné adaptéry vstupov, arbitráž, existujúci multiroom a overené externé protokoly. |
| **v0.0.4** | Ovládam zariadenie cez MQTT, spúšťam nadväzujúce akcie a používam ho ako HA hlasový satelit. | Stabilné príkazy/stavy, udalosti, audio odbočka pred lokálnou hlasovou vrstvou. |
| **v0.0.5** | Používam overené Matter ovládanie a celý dohodnutý rozsah na podporovaných RPi/HiFiBerry kombináciách. | Dokončené funkcie v0.0.1–v0.0.4, overenie Matter, kompatibility a regresií. |

## 3. v0.0.1 – samostatný prehrávač a systémový základ

### Funkcie

- Bootovateľný Yocto image pre prvú referenčnú kombináciu Raspberry Pi 3+ a HiFiBerry. Read-only systém, oddelená zapisovateľná konfigurácia a používateľské dáta, ohraničené logy.
- Prvé nastavenie cez Ethernet/DHCP: názov zariadenia, administrátorský účet, audio výstup a lokálna hlasitosť. Obnovenie konfigurácie po reštarte, bez predvoleného automatického prehrávania.
- Lokálne súbory z určenej dátovej cesty alebo pripojeného USB úložiska: WAV/PCM, FLAC, MP3, AAC/M4A a Ogg Vorbis. Základný výber súboru/adresára, prehratie a metadáta, ktoré zdroj poskytuje.
- Internetové rádio zo zadanej URL, uložené stanice, HTTP/HTTPS MP3/AAC a jednoduché M3U/PLS playlisty. Jasné chyby neplatnej URL, nepodporovaného formátu a nedostupného zdroja.
- Spoločná pipeline stereo PCM 48 kHz / 24 bit, prevzorkovanie podporovaných vstupov, lokálna hlasitosť a mute. Play/pause/stop iba podľa schopností zdroja; nepodporované príkazy sa odmietnu.
- Jeden správca prehrávacej relácie, najviac jeden vlastník hudby, deterministické poradie udalostí, identifikátory relácií a odpojenie starého zdroja pred novým. Bez návratu k predchádzajúcemu vstupu po skončení relácie.
- Lokálne API a webUI: vstup, stav, metadáta, ovládanie, výber lokálnej hudby a rádia, audio nastavenia, verzia image a základná diagnostika. WebUI používa rovnaký model príkazov ako API.
- HTTPS, autentifikácia webUI/API vrátane uploadu, ochrana relácie v prehliadači, hashované heslo, ochrana tajomstiev v úložisku/logoch a minimálne oprávnenia služieb.
- Manuálny upload image, kontrola úplnosti/formátu/hardvéru, zápis do neaktívneho A/B slotu, skúšobný boot a automatický rollback podľa UPD-06. Bez online aktualizácií a podpisovania.
- Záloha a obnova konfigurácie cez autentifikované rozhranie; rozpoznanie verzie konfigurácie a overenie obnovy aj po rollbacku. Používateľské hudobné dáta sa aktualizáciou neprepisujú.
- Open-source repozitár s vlastným kódom, návodom na zostavenie/flash a licenčnými informáciami. Predbežná licencia vlastného kódu Apache-2.0 sa potvrdí po kontrole závislostí; licencie externých komponentov zostávajú zachované.

### Príprava ďalších verzií

- Zaznamenať architektúru audio odbočky, stavov relácie, API a dátových migrácií tak, aby ďalšie vstupy nevyžadovali samostatné riadenie výstupu.
- Urobiť krátke technické overenie kandidátov na multiroom, AirPlay 2, Spotify, MA endpoint, HA Voice a Matter commissioning bez certifikácie. Toto nie je dodanie týchto funkcií; výsledkom sú známe obmedzenia, licencie a plán ich implementácie.
- Spísať konkrétny zoznam cieľových modelov RPi 3+ a bežne rozšírených HiFiBerry kariet/revízií. Matica uvádza aj relevantné funkcie a platné kombinácie dosky/karty; nepožaduje fyzicky nemožné kombinácie. Zoznam sa pri objavení ďalšej bežnej karty doplní, nie potichu zúži.
- Vybrať referenčné klienty a controllery pre neskoršie testy; ich názvy a verzie budú súčasťou výsledkov.

### Podmienky vydania

- Úspešný prvotný štart a nastavenie na referenčnej kombinácii; API/webUI dostupné do 90 sekúnd.
- Overené súbory každého základného formátu, rádio cez HTTP aj HTTPS, obidva playlistové formáty a zrozumiteľné odmietnutie nepodporovaného vstupu.
- Aspoň 2 hodiny prehrávania bez pádu a samovoľného prepnutia; potvrdenie bežného príkazu do 300 ms, dostupný lokálny súbor/priamy stream začne do 5 sekúnd.
- Lokálne ovládanie a súbory fungujú bez internetu. Nedostupné rádio neblokuje UI ani inú lokálnu hudbu.
- Overený upload/inštalácia, odmietnutie nesprávneho image, prerušený upload, rollback nefunkčného slotu a výpadok napájania počas zápisu neaktívneho slotu. Zachovaná konfigurácia a používateľské dáta.
- API ani upload nie sú po prvotnom nastavení dostupné bez autentifikácie. Záloha sa úspešne obnoví.

## 4. v0.0.2 – NAS a vlastný multiroom

### Funkcie

- NAS cez SMB 2/3 aj NFSv4 v režime iba na čítanie: konfigurácia servera/share/cesty, potrebné prihlasovanie, prehliadanie súborov a prehrávanie rovnakých základných formátov ako lokálne. Chyba NAS nezablokuje ostatné vstupy.
- Konfigurácia Wi-Fi cez webUI z existujúceho Ethernet pripojenia, zobrazenie sieťového stavu a diagnostiky. Captive portal nie je požiadavkou.
- Vlastná skupina s jedným masterom a najviac tromi satelitmi. Výber mastera cez IP alebo hostname/názov, uložené členstvo oddelené od aktuálneho prehrávania.
- Synchronizovaný vstup Master, distribúcia lokálneho/NAS zdroja a rádia. Žiadne preposielanie vstupu Master do ďalšej skupiny, odmietnutie vlastnej adresy a zisteného cyklu.
- Lokálne prevzatie satelitu bez zásahu do ostatných; zmena samostatného vstupu mastera prevezme jeho satelity. Rozhranie adaptérov už rezervuje výnimku MA, reálne overenú vo v0.0.3.
- Zapínateľné centrálne riadenie hlasitosti, nezávislá uložená referencia každého satelitu, V = G × R / 100, predvolené hodnoty a správanie pri zapnutí/vypnutí podľa kapitoly 5.1.
- Lokálna zmena hlasitosti satelitu nemení referenciu ani ostatné zariadenia. Ďalšia skupinová zmena znovu použije referenciu. Zmena hlasitosti sama neprevezme iný vstup.
- Skupinové play/pause/stop a samostatné lokálne ovládanie satelitu. Lokálne aj skupinové mute s logikou OR; skupinové unmute zachová lokálne mute.
- Pri strate mastera ticho bez failoveru, zahodenie starého audia, opakované pripojenie a návrat na aktuálnu pozíciu tej istej aktívnej relácie. Lokálny stop/pause alebo zmena vstupu sa rešpektujú.
- API/webUI pre členstvo, stav skupiny, referencie, centrálne riadenie a odlíšenie lokálnych/skupinových príkazov. Prístupový kľúč skupiny a ochrana NAS/Wi-Fi tajomstiev.
- Prvé rozšírenie hardvérovej matice nad referenčnú kombináciu, vrátane správania softvérovej hlasitosti pri rôznych audio výstupoch.

### Podmienky vydania

- NAS SMB aj NFS sa pripoja, prehrajú a po chybe sa zariadenie dá ovládať a prepnúť na lokálny zdroj.
- Postupne overená skupina 2 aj 4 fyzických zariadení. Na káblovej sieti odchýlka elektrických výstupov do 2 ms po ustálení a kontrola driftu 30 minút.
- Pripojenie na aktuálnu pozíciu do 5 sekúnd od spojenia; po strate streamu ticho do 2 sekúnd, návrat aktívnej relácie do 15 sekúnd od obnovy stabilného spojenia.
- Splnené scenáre referencií, centrálnej hlasitosti, lokálneho pause/stop, skupinového mute a straty jedného satelitu bez zastavenia ostatných.
- Bez internetu fungujú NAS v lokálnej sieti, vlastný multiroom aj ovládanie. Wi-Fi sa otestuje samostatne s uvedením podmienok a nameraných výsledkov.
- Aktualizácia z v0.0.1 a rollback zachovajú konfiguráciu; nové referencie/členstvo sa pri migrácii spracujú spôsobom kompatibilným s návratom.

## 5. v0.0.3 – prijímacie protokoly a Music Assistant

### Funkcie

- Spotify Connect prijímač, ktorý je možné vybrať v aplikácii Spotify; stav a dostupné metadáta vo webUI/API.
- AirPlay 2 prijímač. Prípadný režim AirPlay 1 je voliteľné technické rozhodnutie a nenahrádza požiadavku AirPlay 2.
- Bluetooth audio prijímač s explicitným párovaním a správou spárovaných klientov; bez požiadavky odosielania zvuku cez Bluetooth.
- Príjem Apple Music a SoundCloudu cez podporované protokoly, najmä AirPlay. Bez vlastného prehliadania ich katalógov.
- Music Assistant endpoint s konkrétnym overeným protokolom, discovery, stavom a oznamovaním lokálneho prevzatia. MA zodpovedá za svoje skupiny a distribúciu.
- Každý nový adaptér používa spoločného správcu relácií. Pripojenie bez audia neprevezme výstup, pokračovanie starého streamu sa nepovažuje za nový začiatok. Oneskorené callbacky sa odmietnu podľa identity relácie.
- Samostatné vstupy mastera možno poslať do vlastnej skupiny. MA sa nikdy neposiela cez vlastný multiroom: pri prevzatí mastera sa vlastná distribúcia zastaví, čakajúce satelity stíchnu a ich konfigurácia zostáva.
- MA môže samostatne prevziať každý endpoint. Zmena samostatného vstupu mastera, skupinová hlasitosť, mute a transport neprevezmú endpoint, ktorý aktuálne riadi MA.
- Lokálny nový vstup prevezme iba príslušný endpoint MA. Ďalší nový začiatok MA môže prevziať endpoint späť; samotný pokračujúci stream nie. Ukončenie MA neobnoví starý zdroj.
- Rozšírené webUI/API s dostupnosťou vstupov, párovaním Bluetooth, stavmi relácií a skutočne podporovanými transportnými príkazmi.
- Overenie zdrojových streamov, účtov, závislostí a licencií pre zvolené komponenty; konkrétne obmedzenia klientov sa dokumentujú.

### Podmienky vydania

- Reálne prehrávanie z referenčných Spotify, AirPlay 2 a Bluetooth klientov; klienti a ich verzie uvedení vo výsledkoch. Pri Apple Music/SoundCloud overená cesta cez podporovaný protokol.
- Reálna MA skupina aspoň dvoch endpointov a samostatná vlastná skupina. Splnené všetky scenáre kapitoly 6.1, vrátane MA iba na masteri a MA na vybranom satelite.
- Overené dvojice preberania všetkých dostupných vstupov, súbežné štarty, ukončenie starej relácie aj pretrvávajúci starý stream.
- Všetky dodané samostatné vstupy prehrávajú aj cez vlastný multiroom; MA ostáva izolovaný. Vstup nepodporujúci konkrétny príkaz nevykazuje falošný úspech.
- Odpojenie klienta, nedostupný účet/služba alebo MA nevyvolajú pád, návrat starého zdroja ani rollback image.
- Regresia aktualizácií, lokálnych/NAS zdrojov, skupinovej hlasitosti a bezpečného párovania.

## 6. v0.0.4 – MQTT, akcie a Home Assistant Voice

### Funkcie

- MQTT príkazy, stav a udalosti s dokumentovanými témami/payloadmi, jednoznačnou identitou zariadenia a rovnakým správaním ako API. MQTT je voliteľné; nepripojený broker neblokuje prehrávanie.
- Konfigurácia brokera a prihlasovania, MQTT TLS s overovaním certifikátu; explicitná voľba nešifrovaného lokálneho MQTT. Tajomstvá sa nezverejňujú v stavoch ani logoch.
- Udalosti pripravenosti zariadenia, prehrávania, vstupu, hlasitosti/mute, dostupnosti mastera a výsledku aktualizácie podľa kapitoly 9. Udalosť zmeny iba pri skutočnej zmene.
- Najviac 10 nakonfigurovaných akcií na udalosť: lokálne príkazy, HTTP GET/POST a MQTT publish. Konfigurácia aj výsledky dostupné cez webUI/API.
- Poradie, oneskorenia 0–60 s, timeout 3 s, pokračovanie po chybe, ohraničená fronta a ochrana proti slučkám podľa špecifikácie. Bez shell skriptov, automatických retry alebo trvalej fronty po reštarte.
- Scenár zapnutia externého zosilňovača cez HTTP/MQTT pri začiatku prehrávania. Výslovne nastavená štartovacia akcia je jediná lokálna výnimka z predvoleného nespúšťania hudby po bootovaní.
- HA Voice satelit s USB mikrofónom: výber mikrofónu, overený wake-word mechanizmus, transport do HA, zachytenie hlasu a prehratie odpovede. Rozpoznávanie a odpoveď môžu bežať mimo RPi.
- Odpoveď iba na oslovenom zariadení. Hudobná relácia pokračuje, lokálna hlasová vrstva nemení vstup/vlastníka, členstvo ani synchronizáciu.
- Na masteri odbočka do multiroom pred lokálnou hlasitosťou/mute a Voice vrstvou; do ostatných miestností nejde odpoveď ani dočasné stíšenie. Rovnaká izolácia pri MA.
- Predvolené stíšenie −20 dB, nábeh/návrat približne 200 ms, limit interakcie 60 s a odstránenie stíšenia aj pri chybe/zrušení. Aktuálna hlasitosť, mute, nový vstup alebo stop používateľa sa neprepíšu starým stavom.
- Hlasové príkazy na ovládanie hudby používajú bežné pravidlá príkazov; samotná hlasová odpoveď nie je nový hudobný vstup. Potlačenie ozveny a zvolená mikrofónová cesta sa overia na reálnom zapojení.
- WebUI/API pre MQTT, akcie, USB mikrofón, stav Voice a diagnostiku interakcie bez nahrávania hlasu do bežných logov.

### Podmienky vydania

- Rovnaký príkaz cez API, webUI a MQTT má rovnaký výsledný stav a dosah. Otestované MQTT prihlasovanie, TLS, výpadok brokera a obnovenie spojenia bez prehratia starých príkazov.
- Overený limit 10 akcií, poradie, timeout, zlyhanie HTTP/MQTT, preplnenie fronty, reštart a deaktivácia pravidla pri opakovanej spätnej väzbe. Žiadny test nesmie blokovať audio.
- Úspešná hlasová interakcia na masteri, satelite aj MA endpointe cez USB mikrofón počas prehrávania hudby.
- Splnené všetky scenáre kapitoly 7.1 vrátane nedostupného HA, chýbajúcej odpovede, zmeny hlasitosti, nového vstupu a zastavenia hudby počas interakcie.
- Na ostatných audio výstupoch sa meraním/odposluchom nepotvrdí únik odpovede ani lokálneho stíšenia; časová synchronizácia hudby zostáva zachovaná.
- Funguje kombinácia NAS/rádio/prijímací vstup → vlastný multiroom → Voice na jednom zariadení → MQTT/HTTP akcia. MA zostáva mimo vlastnej distribúcie.

## 7. v0.0.5 – Matter, úplná kompatibilita a uzavretie rozsahu

### Funkcie

- Matter ako ovládané zariadenie, bez úlohy controllera/bridge a bez požiadavky na certifikáciu. Skutočné párovanie, riadenie prístupov, ovládanie a hlásenie stavu v zvolených overených ekosystémoch.
- Matica funkcia → štandardný device type/cluster → controller. Pre všetky funkcie, ktoré možno korektne reprezentovať zvolenými štandardnými clustrami, sa implementuje dostupné ovládanie/stav; pri nepokrytej funkcii zostáva API/MQTT/webUI.
- Matica odlíši nepokrytie štandardom od obmedzenia konkrétneho controllera. Dokumentácia samotná nenahrádza fungujúcu Matter integráciu. Rovnaký rozsah v každom ekosystéme sa nesľubuje.
- Matter príkazy používajú rovnakého správcu a rešpektujú lokálny/skupinový dosah, vlastníctvo MA a reálne schopnosti vstupu.
- Dokončená a odmeraná kompatibilita cieľových RPi 3+ a všetkých bežne rozšírených HiFiBerry kariet z priebežne udržiavaného zoznamu: audio výstup, konfigurácia, hlasitosť/mute, reštart, aktualizácia a multiroom.
- Rozšírenie potrebných image variantov, ovládačov a nastavení pre platné kombinácie. Žiadna kombinácia sa neoznačí za overenú iba na základe rovnakej rodiny karty alebo RPi.
- Dokončené základné webUI pre všetky dohodnuté funkcie: zdroje/metadáta, NAS, sieť/audio, skupiny/referencie, Bluetooth, MA stav, MQTT/akcie, Voice, Matter, manuálny update, zálohu/obnovu a diagnostiku.
- Stabilné zdokumentované API a MQTT kontrakty, chyby, schopnosti vstupov a diagnostika relácií. Export diagnostiky nezahrnie heslá, tokeny, skupinové kľúče ani hlasové nahrávky.
- Overené migrácie konfigurácie medzi vydaniami, rollback, záloha/obnova na podporovanom hardvéri a zachovanie lokálnych hudobných dát.
- Open-source vydanie so zdrojmi vlastného kódu a zostavenia, licenčnými informáciami externých komponentov, potvrdenou licenciou vlastného kódu, návodmi a release notes pre všetky funkcie.

### Podmienky vydania

- Otestované Matter párovanie a všetky funkcie označené v matici za podporované na konkrétnych controlleroch. Prípadný štandardne nereprezentovateľný príkaz zostáva funkčný cez ostatné dohodnuté rozhrania.
- Každá požadovaná platná hardvérová kombinácia má výsledok. Neoverená alebo nekompatibilná požadovaná karta nie je splnením SYS-02; zostáva blokujúcim nedodaným rozsahom, kým sa neopraví alebo sa výslovne nezmení dohoda.
- Kompletná regresia všetkých požiadaviek a scenárov špecifikácie vrátane 4 zariadení, všetkých vstupov, MA izolácie, Voice, akcií a súbežného ovládania cez rozhrania.
- Minimálne 8 hodín zmiešanej prevádzky na referenčnom zapojení bez pádu, samovoľného prepnutia alebo rastu fronty/logov bez hraníc; rovnaké synchronizačné ciele ako vo v0.0.2. Každá podporovaná kombinácia navyše prejde základným testom prehrávania a systémových funkcií.
- Manuálny upgrade cez predchádzajúce vydania a návrat pri neúspešnom skúšobnom boote; NAS/MA/HA/internet nedostupné pri štarte nevyvolajú chybný rollback.
- Matica pokrytia nižšie nemá nedodanú povinnú položku. Známa chyba môže zostať iba vtedy, ak neznemožňuje dohodnutú funkciu ani jej bezpečné použitie a je jasne uvedená.

## 8. Matica pokrytia požiadaviek

„Začiatok“ znamená prvé použiteľné dodanie pre vtedy dostupné zdroje/hardvér. „Úplné funkčné dodanie“ určuje, dokedy musí byť požiadavka splnená vrátane závislostí, napríklad výnimiek MA alebo Voice. Všetky dodané požiadavky sa opäť overia pri v0.0.5. Žiadny riadok nie je evidenciou už vykonanej práce.

| ID | Začiatok | Úplné funkčné dodanie | Pokrytie |
| --- | --- | --- | --- |
| SYS-01 | v0.0.1 | v0.0.5 | Referenčné RPi, postupne celá cieľová matica RPi 3+. |
| SYS-02 | v0.0.1 | v0.0.5 | Referenčná HiFiBerry, potom všetky požadované bežné karty a platné kombinácie. |
| SYS-03 | v0.0.1 | v0.0.1 | Yocto image a zostavenie. |
| SYS-04 | v0.0.1 | v0.0.1 | Read-only systém a oddelené perzistentné dáta. |
| SYS-05 | v0.0.1 | v0.0.1 | Funkčný A/B update a rollback. |
| SYS-06 | v0.0.1 | v0.0.5 | Zdrojové vydania, zostavenie a licenčné podklady pre všetky dodané komponenty. |
| SYS-07 | v0.0.1 | v0.0.1 | Výhradne manuálny image, bez online mechanizmu a podpisovania vo všetkých vydaniach plánu. |
| IN-01 | v0.0.1 | v0.0.1 | Lokálne súbory a základné formáty. |
| IN-02 | v0.0.2 | v0.0.2 | NAS SMB 2/3 a NFSv4, prehliadanie a prehrávanie iba na čítanie. |
| IN-03 | v0.0.3 | v0.0.3 | Spotify Connect prijímač. |
| IN-04 | v0.0.3 | v0.0.3 | AirPlay 2 vrátane využitia Apple Music/SoundCloud cez protokol. |
| IN-05 | v0.0.3 | v0.0.3 | Bluetooth prijímač a párovanie. |
| IN-06 | v0.0.1 | v0.0.1 | HTTP/HTTPS rádiá a M3U/PLS. |
| IN-07 | v0.0.3 | v0.0.3 | Overený Music Assistant endpoint. |
| IN-08 | v0.0.2 | v0.0.2 | Vstup Master cez IP/hostname. |
| SEL-01 | v0.0.1 | v0.0.3 | Posledný nový začiatok vyhráva, postupne pre všetky hudobné vstupy. |
| SEL-02 | v0.0.1 | v0.0.3 | Pripojenie bez prehrávania nepreberá výstup. |
| SEL-03 | v0.0.2 | v0.0.3 | Lokálny zásah satelitu vrátane MA izolácie. |
| SEL-04 | v0.0.2 | v0.0.3 | Prevzatie vlastnej skupiny a odlišný prechod na MA. |
| SEL-05 | v0.0.1 | v0.0.3 | Bez automatického návratu zdroja vrátane skončenia MA. |
| SEL-06 | v0.0.1 | v0.0.3 | Jediný vlastník, uložené členstvo oddelené od relácie. |
| SEL-07 | v0.0.1 | v0.0.3 | Spoločný správca pre všetky hudobné adaptéry. |
| MR-01 | v0.0.2 | v0.0.2 | Jeden master a synchronizované satelity. |
| MR-02 | v0.0.2 | v0.0.2 | Uložený master cez IP/hostname. |
| MR-03 | v0.0.2 | v0.0.2 | Ticho pri strate bez automatického náhradného mastera. |
| MR-04 | v0.0.2 | v0.0.3 | Samostatný vstup satelitu vrátane nových prijímačov. |
| MR-05 | v0.0.2 | v0.0.3 | Prevzatie satelitov so zachovaním výnimky MA. |
| MR-06 | v0.0.2 | v0.0.3 | Iba lokálny dosah ovládania satelitu, aj pri MA. |
| MR-07 | v0.0.2 | v0.0.2 | Centrálna hlasitosť, referencie a skupinový transport/mute. |
| MR-08 | v0.0.2 | v0.0.3 | Zachované členstvo, žiadne skupinové ovládanie MA endpointu. |
| VOL-01 | v0.0.2 | v0.0.2 | Zapnutie/vypnutie centrálnej hlasitosti. |
| VOL-02 | v0.0.2 | v0.0.2 | Uložená referencia každého satelitu a výpočet výslednej hlasitosti. |
| VOL-03 | v0.0.2 | v0.0.2 | Lokálny zásah neprepíše referenciu. |
| VOL-04 | v0.0.2 | v0.0.3 | Skupinová hlasitosť nepreberá iný vstup ani MA. |
| VOL-05 | v0.0.2 | v0.0.2 | Zachovanie hlasitostí/referencií pri vypnutí a definované zapnutie. |
| VOL-06 | v0.0.4 | v0.0.4 | Voice stíšenie až po výpočte hudobnej hlasitosti. |
| TRN-01 | v0.0.1 | v0.0.3 | Zdrojové schopnosti a transport mastera pre dodané samostatné vstupy. |
| TRN-02 | v0.0.1 | v0.0.3 | Explicitné obnovenie alebo čakanie na externého klienta. |
| TRN-03 | v0.0.2 | v0.0.2 | Lokálny pause/stop satelitu a návrat na aktuálnu pozíciu. |
| TRN-04 | v0.0.2 | v0.0.2 | Nezávislé skupinové a lokálne mute. |
| TRN-05 | v0.0.2 | v0.0.3 | Rozlíšený dosah UI/API a izolácia MA ovládania. |
| REC-01 | v0.0.1 | v0.0.4 | Obnova nastavení bez autoplay; explicitná štartovacia akcia od v0.0.4. |
| REC-02 | v0.0.2 | v0.0.2 | Návrat tej istej relácie po prerušení spojenia. |
| REC-03 | v0.0.2 | v0.0.2 | Odstupy pokusov a rešpektovanie lokálneho stop/prepnutia. |
| REC-04 | v0.0.2 | v0.0.2 | Odmietnutie cyklu a jednej ďalšej úrovne distribúcie. |
| MA-01 | v0.0.3 | v0.0.3 | Použiteľný MA endpoint. |
| MA-02 | v0.0.3 | v0.0.3 | MA je jediným nadriadeným riadením svojej cesty. |
| MA-03 | v0.0.3 | v0.0.3 | MA riadi vlastné skupiny bez vnorenej vlastnej distribúcie. |
| MA-04 | v0.0.2 | v0.0.3 | Vlastný multiroom zostáva samostatne dostupný popri MA. |
| MA-05 | v0.0.3 | v0.0.3 | Lokálne prevzatie nemení ostatné MA endpointy. |
| MA-06 | v0.0.3 | v0.0.3 | Žiadne posielanie MA audia do vlastného multiroom. |
| MA-07 | v0.0.3 | v0.0.3 | Prevzatie mastera MA zastaví vlastnú distribúciu. |
| MA-08 | v0.0.3 | v0.0.3 | Nový začiatok versus pokračujúci stream a lokálna arbitráž. |
| VOICE-01 | v0.0.4 | v0.0.4 | USB mikrofón a HA Voice satelit. |
| VOICE-02 | v0.0.4 | v0.0.4 | Iba lokálne stíšenie, hudba pokračuje synchronizovane. |
| VOICE-03 | v0.0.4 | v0.0.4 | Zachytenie hlasu a lokálna odpoveď. |
| VOICE-04 | v0.0.4 | v0.0.4 | Hlasová vrstva nepreberá hudobný vstup/vlastníka. |
| VOICE-05 | v0.0.4 | v0.0.4 | Odbočka pred lokálnou vrstvou, izolácia aj pri MA. |
| VOICE-06 | v0.0.4 | v0.0.4 | Odstránenie stíšenia po skončení, chybe aj timeoute. |
| VOICE-07 | v0.0.4 | v0.0.4 | Zachovanie aktuálnej hlasitosti a mute. |
| VOICE-08 | v0.0.4 | v0.0.4 | Žiadne obnovenie starého vstupu ani zastavenej hudby. |
| CTL-01 | v0.0.1 | v0.0.5 | API rozširované spolu s funkciami, úplný kontrakt do v0.0.5. |
| CTL-02 | v0.0.4 | v0.0.5 | MQTT príkazy/stavy/udalosti; doplnenie stavov funkcií v0.0.5. |
| CTL-03 | v0.0.5 | v0.0.5 | Reálna Matter integrácia a overená matica štandardných funkcií. |
| CTL-04 | v0.0.1 | v0.0.5 | Postupne celé základné webUI vrátane všetkých nastavení. |
| ACT-01 | v0.0.4 | v0.0.4 | Limit najviac 10 akcií na udalosť. |
| ACT-02 | v0.0.4 | v0.0.4 | Lokálne, HTTP a MQTT akcie. |
| ACT-03 | v0.0.4 | v0.0.4 | Celý dohodnutý katalóg bežných udalostí. |
| UPD-01 | v0.0.1 | v0.0.1 | Manuálny upload cez lokálne webUI, bez internetu. |
| UPD-02 | v0.0.1 | v0.0.1 | Bez online vyhľadávania, downloadu a automatických aktualizácií. |
| UPD-03 | v0.0.1 | v0.0.1 | Bez podpisovania/overovania podpisov v plánovanej sérii. |
| UPD-04 | v0.0.1 | v0.0.1 | A/B a kontrola formátu, úplnosti a kompatibility. |
| UPD-05 | v0.0.1 | v0.0.1 | Staging uploadu a zápis neaktívneho slotu. |
| UPD-06 | v0.0.1 | v0.0.1 | Health check, limity bootu a návrat bez závislosti od externých služieb. |

### Požiadavky bez samostatného ID

| Oblasť špecifikácie/dohody | Dodanie |
| --- | --- |
| Práca bez cloudu projektu | v0.0.1 lokálne, v0.0.2 vlastný multiroom/NAS; regresia v každom vydaní. |
| Základné kodeky, rádiové playlisty a PCM pipeline | v0.0.1; rovnaké súborové minimum pre NAS od v0.0.2. |
| Sieť a prvotné nastavenie | Ethernet/DHCP a admin v0.0.1; Wi-Fi cez webUI v0.0.2. |
| Bezpečnosť a tajomstvá | Základ API/uploadu v0.0.1; skupinový kľúč a NAS/Wi-Fi v0.0.2; klienti/párovanie v0.0.3; MQTT/akcie/Voice v0.0.4; Matter a úplná regresia v0.0.5. |
| Záloha, obnova, perzistencia a schémy pri rollbacku | Od v0.0.1, rozšírenie a regresia pri každom vydaní. |
| Apple Music a SoundCloud cez prijímací protokol | v0.0.3 cez overenú cestu, bez samostatných katalógov. |
| Počet zariadení, synchronizácia, zotavenie | v0.0.2 podľa cieľov špecifikácie; rozšírené vstupy v0.0.3, Voice v0.0.4, úplná regresia v0.0.5. |
| −20 dB Voice stíšenie, približne 200 ms a limit 60 s | v0.0.4. |
| Diagnostika a chybové stavy | Od v0.0.1 pre dodané funkcie; úplná a bez tajomstiev vo v0.0.5. |
| Licencia vlastného kódu, licencie komponentov, zostavenie a zverejnenie | Od v0.0.1 pri každom vydaní, kompletný rozsah vo v0.0.5. |

## 9. Čo sa do v0.0.5 nepridáva

Zostávajú platné pôvodné vylúčenia: Zigbee, Google Cast ako povinný vstup, Bluetooth vysielač, priame katalógové integrácie Apple Music/YouTube Music/SoundCloud, samostatná priama integrácia prehrávača do Home Assistant, Matter certifikácia/controller/bridge, viac masterov jednej vlastnej skupiny a automatický failover. MA a HA Voice zostávajú povinné nezávislé funkcie.

Nepridávajú sa online aktualizácie ani podpisovanie image. Bit-perfect, DSD, multikanál, rozdielne interné formáty satelitov, captive portal, ľubovoľné shell akcie a trvalá fronta automatizácií nie sú podmienkou týchto piatich verzií.
