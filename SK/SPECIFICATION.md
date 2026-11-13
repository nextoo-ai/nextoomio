# nextoomio – funkčná špecifikácia

Verzia dokumentu: **1.6**  
Verzia prvého prototypu: **nextoomio v0.0.1**  
Plánovaný rozsah dodania: **v0.0.1–v0.0.5 podľa [ROADMAP.md](ROADMAP.md)**  
Dátum: **2026-11-13**  
Stav: **Funkčná špecifikácia série prototypov v0.0.1–v0.0.5; pravidlá a akceptačné ciele sa dodávajú postupne podľa roadmapy. Implementácia a hardvérové merania zatiaľ nie sú potvrdené.**  
Názov projektu: **nextoomio**

## 1. Cieľ projektu

Vytvoriť open-source náhradu Volumia pre Raspberry Pi a HiFiBerry. Zariadenie má samostatne prehrávať hudbu, prijímať zvuk z bežných klientov, podporovať synchronizovaný multiroom a fungovať ako endpoint pre Music Assistant a hlasový satelit Home Assistant.

Lokálne ovládanie, konfigurácia a vlastný multiroom nemajú vyžadovať cloud projektu. Internetové hudobné služby môžu vyžadovať internet a používateľský účet.

Dokument zaznamenáva výsledné rozhodnutia z diskusie a predvolené pravidlá série prototypov. Uvedenie požiadavky samo osebe nepotvrdzuje jej realizovateľnosť pre každý hardvér či externý ekosystém. v0.0.1 overí základné prehrávanie na reálnom hardvéri; v0.0.2 až v0.0.5 postupne doplnia celý dohodnutý rozsah podľa [ROADMAP.md](ROADMAP.md). Cieľová podpora Raspberry Pi 3+ a bežne rozšírených HiFiBerry kariet zostáva zachovaná, vydanie však musí uvádzať konkrétne otestované kombinácie. Špecifikácia určuje správanie funkcií, roadmapa určuje verziu ich dodania.

## 2. Hardvér a systém

| ID | Požiadavka |
| --- | --- |
| SYS-01 | Cieľovou platformou je Raspberry Pi 3 a novšie generácie (RPi 3+). Konkrétne modely a hardvérové revízie sa uvedú v matici kompatibility. |
| SYS-02 | Požadovaná je podpora všetkých bežne rozšírených HiFiBerry kariet. Ich konkrétny zoznam a overené kombinácie s modelmi a revíziami Raspberry Pi 3 a novších sa uvedú v matici kompatibility. |
| SYS-03 | Operačný systém bude postavený na Yocto Linuxe. |
| SYS-04 | Systémový filesystem bude iba na čítanie. Zapisovateľná konfigurácia a dáta musia byť riešené oddelene. |
| SYS-05 | Aktualizácie budú používať A/B mechanizmus. |
| SYS-06 | Projekt bude zverejnený ako open source. |
| SYS-07 | Aktualizácia sa vykonáva výhradne manuálnym uploadom systémového image. V sérii v0.0.1–v0.0.5 nie sú plánované online aktualizácie ani podpisovanie a overovanie podpisov image. |

## 3. Hudobné zdroje a vstupy

| ID | Požiadavka |
| --- | --- |
| IN-01 | Prehrávanie lokálnych hudobných súborov. |
| IN-02 | Prehrávanie hudby z NAS pomocou bežných protokolov. |
| IN-03 | Spotify Connect prijímač: zariadenie možno vybrať v aplikácii Spotify. |
| IN-04 | AirPlay prijímač; základným cieľom je AirPlay 2. AirPlay 1 nie je povinný, prípadný kompatibilitný režim zostáva technickým rozhodnutím. |
| IN-05 | Bluetooth audio prijímač. Odosielanie zvuku cez Bluetooth sa nepožaduje. |
| IN-06 | Prehrávanie internetových rádií. |
| IN-07 | Príjem prehrávania z Music Assistant. |
| IN-08 | Vstup „Master“ pre príjem zvuku z mastera vlastného multiroom. Master sa určuje IP adresou alebo hostname/názvom zariadenia. |

Počiatočné minimum pre súbory je WAV/PCM, FLAC, MP3, AAC v M4A a Ogg Vorbis: lokálne od v0.0.1 a na NAS od v0.0.2. NAS sa pripája cez SMB 2/3 alebo NFSv4 iba na čítanie; SMB1 sa nepožaduje. Internetové rádiá od v0.0.1 používajú HTTP/HTTPS streamy MP3 alebo AAC a jednoduché playlisty M3U/PLS. Rozšírenia formátov a podporované profily sa doplnia podľa overenia dekodérov; nejde o prísľub podpory všetkých profilov uvedených kontajnerov.

Séria prototypov používa spoločný interný hudobný formát stereo PCM 48 kHz / 24 bit. Iné podporované vzorkovacie frekvencie sa prevzorkujú. Bit-perfect prehrávanie, DSD, multikanál a individuálne formáty pre každý satelit nie sú podmienkou série v0.0.1–v0.0.5. Pri nepodporovanom súbore alebo streame sa zobrazí konkrétna chyba bez pádu prehrávača.

Pri Apple Music a SoundCloude je požadovaný príjem cez podporované vstupné protokoly, najmä AirPlay. Samostatné prehliadanie ich katalógov a priame prehrávanie cez vlastné integrácie nie sú súčasťou dohodnutého rozsahu. Google Cast prijímač nebol zaradený medzi povinné vstupy.

## 4. Automatické preberanie vstupu

- **SEL-01:** Na danom zariadení platí „posledný prichádzajúci vstup vyhráva“: automaticky prepne na vstup, ktorý začne prehrávať.
- **SEL-02:** Samotné pripojenie klienta bez prehrávania nesmie byť dôvodom prevziať výstup.
- **SEL-03:** Zmena vstupu na slave ovplyvňuje iba tento slave.
- **SEL-04:** Zmena samostatného vstupu na masteri vlastného multiroom sa prenesie na jeho skupinu podľa MR-05. Prechod mastera na Music Assistant sa riadi MA-07.
- **SEL-05:** Ukončenie prehrávania samo neobnovuje predchádzajúci zdroj. Ďalšie prehrávanie vyžaduje nový začiatok prehrávania alebo explicitný príkaz; samotné skončenie Music Assistant neobnoví predchádzajúcu reláciu ani príjem zo vstupu Master.
- **SEL-06:** Na zariadení smie byť aktívny najviac jeden vlastník hudobného prehrávania: samostatný vstup, vlastný multiroom alebo Music Assistant. V nečinnosti nie je aktívny žiadny vlastník. Uložené členstvo v skupine a samotné pripojenie klienta nepredstavujú vlastníctvo prehrávania.
- **SEL-07:** O prevzatí prehrávania a zmene audio cesty rozhoduje jeden spoločný správca prehrávacej relácie na zariadení. Adaptéry vstupov mu odovzdávajú požiadavky; nesmú nezávisle preberať audio výstup ani riadiť cudzie skupiny.

Samostatnými vstupmi sa v pravidlách arbitráže a vlastného multiroom rozumejú lokálne/NAS súbory, Spotify Connect, AirPlay, Bluetooth a internetové rádiá (IN-01 až IN-06). Vstup Master a Music Assistant majú samostatné pravidlá uvedené nižšie. Pravidlo posledného vstupu platí pre začiatok prehrávania adresovaný priamo zariadeniu; prenesené skupinové prevzatie podlieha výnimke v MR-05.

Požiadavky na prevzatie sa spracujú sériovo jedným správcom. Pri súbehu rozhoduje poradie prijatia správcom, pri rovnakom čase jeho monotónne poradové číslo. Každá relácia dostane identifikátor; oneskorené audio a udalosti z predchádzajúcej relácie nesmú prevziať ani zastaviť novú reláciu. Pri prevzatí sa predchádzajúci zdroj zastaví alebo odpojí podľa možností adaptéra; nové audio sa pripojí až po odpojení starej hudobnej cesty. Hlasová interakcia používa samostatnú dočasnú vrstvu podľa kapitoly 7.

## 5. Vlastný multiroom

| ID | Pravidlo |
| --- | --- |
| MR-01 | Skupina používa jedného mastera; slaves prijímajú jeho zvuk synchronizovane. |
| MR-02 | Na slave sa zdrojový master zadáva IP adresou alebo hostname/názvom. |
| MR-03 | Pri strate mastera je na slave používajúcom vstup Master ticho. Automatické zvolenie náhradného mastera sa nepožaduje. |
| MR-04 | Slave môže prepnúť na vlastný vstup bez ovplyvnenia mastera a ostatných členov. |
| MR-05 | Keď master začne prehrávať alebo prepne samostatný vstup, prevezme svoje nakonfigurované slaves vrátane slaves prehrávajúcich vlastný samostatný vstup. Výnimkou sú zariadenia, ktorých aktuálne prehrávanie vlastní Music Assistant; tie zostávajú pod riadením Music Assistant. |
| MR-06 | Ovládanie slave riadi iba tento slave. |
| MR-07 | Master slúži na ovládanie skupiny. Centrálne riadenie hlasitosti možno zapnúť alebo vypnúť a každý satelit má nastaviteľnú referenčnú hlasitosť podľa kapitoly 5.1. Skupinové mute a transportné príkazy sa riadia kapitolou 5.2. |
| MR-08 | Uložené členstvo vo vlastnej skupine je oddelené od aktuálneho vlastníka prehrávania. Lokálne prevzatie alebo prehrávanie cez Music Assistant nemaže konfiguráciu mastera ani členstvo slave. Skupinové príkazy vlastného multiroom nesmú meniť prehrávanie zariadenia, ktoré aktuálne riadi Music Assistant. |

Zariadenia teda nie sú rovnocennými zdrojmi, ktorých každý lokálny vstup preberá celú skupinu. Rozhodujúce sú konečné pravidlá uvedené vyššie.

Transport a spôsob časovej synchronizácie sa vyberú technickým návrhom. Počiatočné merateľné ciele a správanie pri výpadku sú uvedené v kapitolách 5.3 a 13.

### 5.1 Centrálne riadenie a referenčná hlasitosť

- **VOL-01:** Pre vlastnú multiroom skupinu možno na masteri zapnúť alebo vypnúť centrálne riadenie hlasitosti. Pri zapnutí ovládanie hlasitosti na masteri riadi skupinovú hlasitosť; pri vypnutí mení iba lokálnu hlasitosť mastera a nemení hlasitosť satelitov.
- **VOL-02:** Každý satelit musí mať samostatne nastaviteľnú a perzistentne uloženú referenčnú hodnotu hlasitosti. Tá určuje jeho hlasitosť pri skupinovej hlasitosti 100 %. Znižovanie skupinovej hlasitosti znižuje hlasitosť každého satelitu relatívne k jeho referencii; pri skupinovej hlasitosti 0 % je hudobný výstup všetkých riadených členov tichý. Centrálne ovládanie teda nemusí nastavovať rovnakú výslednú hlasitosť na všetkých satelitoch.
- **VOL-03:** Zmena skupinovej hlasitosti nemení uložené referenčné hodnoty satelitov. Lokálna zmena hlasitosti satelitu ovplyvňuje iba tento satelit a sama nemení jeho referenciu. Nasledujúca zmena skupinovej hlasitosti opäť použije skupinovú hodnotu a uloženú referenciu.
- **VOL-04:** Centrálne riadenie sa vzťahuje iba na členov aktuálne prehrávajúcich cez vlastný multiroom. Satelity prehrávajúce samostatný vstup alebo Music Assistant sa nemenia. Samotná zmena skupinovej hlasitosti nesmie prevziať ich vstup.
- **VOL-05:** Vypnutie centrálneho riadenia zachová aktuálne hlasitosti satelitov aj ich uložené referencie. Po zapnutí sa na riadené satelity uplatní aktuálna skupinová hlasitosť vzhľadom na ich referencie.
- **VOL-06:** Lokálne dočasné stíšenie pri hlasovej interakcii sa aplikuje až po výpočte hudobnej hlasitosti zariadenia. Nemení skupinovú hlasitosť ani referencie satelitov.

V prototype sú hlasitosti logické hodnoty 0–100 % s krokom 1 %. Pri centrálnom riadení platí pre satelit `V = G × R / 100`, kde G je skupinová hlasitosť a R jeho referencia; napríklad G = 50 % a R = 60 % znamenajú V = 30 %. Pre lokálny výstup mastera platí V = G. Mapovanie logickej hlasitosti na gain musí byť monotónne a spoločné pre všetky audio cesty; 0 % znamená digitálne ticho. Nejde o percentá akustického výkonu.

Centrálne riadenie je po prvotnom nastavení vypnuté, počiatočná lokálna hlasitosť je 30 % a počiatočná referencia satelitu 100 %. Pri zapnutí sa G nastaví na aktuálnu lokálnu hlasitosť mastera a uplatní na riadené satelity. Pri vypnutí si master aj satelity zachovajú aktuálne výsledné hlasitosti. Referencie a voľba centrálneho riadenia sa ukladajú. Lokálne dočasné stíšenie Voice ani mute nevstupujú do výpočtu G alebo R.

Akceptačné scenáre:

| Scenár | Očakávané správanie |
| --- | --- |
| Dva satelity majú referencie 60 % a 80 %; centrálne riadenie je zapnuté a skupinová hlasitosť je 100 %. | Satelity majú hlasitosti 60 % a 80 %. Zníženie skupinovej hlasitosti zníži obe hlasitosti relatívne k ich referenciám bez zmeny uložených referencií. |
| Centrálne riadenie je vypnuté a používateľ zmení hlasitosť mastera. | Zmení sa iba lokálna hlasitosť mastera; satelity zostanú na svojich aktuálnych hlasitostiach. |
| Jeden satelit prehráva vlastný multiroom, druhý Bluetooth a tretí Music Assistant; používateľ zmení skupinovú hlasitosť. | Zmena sa uplatní iba na masteri a satelite prehrávajúcom vlastný multiroom. Bluetooth a Music Assistant pokračujú bez zmeny hlasitosti alebo vstupu. |
| Používateľ lokálne zmení hlasitosť satelitu a následne zmení skupinovú hlasitosť. | Lokálna zmena neovplyvní ostatné zariadenia ani referenciu. Skupinová zmena opäť nastaví satelit podľa skupinovej hodnoty a jeho uloženej referencie. |

### 5.2 Play, pause, stop a mute

- **TRN-01:** Play/pause/stop na masteri ovládajú aktuálny samostatný zdroj a tým vlastnú distribúciu. Pause sa vykoná iba vtedy, ak ho zdroj podporuje. Nepodporovaný príkaz je vo webUI nedostupný a API ho odmietne konkrétnou chybou; napríklad živé rádio nemusí podporovať pause. Stop ukončí zdroj aj distribúciu a slaves používajúce vstup Master zostanú v tichu.
- **TRN-02:** Play po pause obnoví aktuálnu reláciu. Play po stop je explicitný príkaz na spustenie naposledy zvoleného zdroja, ak ho zariadenie dokáže spustiť. Externý klient sa bez jeho podpory na diaľku nespúšťa; zariadenie namiesto predstierania úspechu oznámi, že čaká na klienta.
- **TRN-03:** Pause/stop na satelite so vstupom Master pozastavia/ukončia iba jeho lokálny príjem. Master ani ostatné satelity neovplyvnia. Play znovu pripojí satelit k aktuálnej pozícii mastera; staré audio sa nedohráva. Pause drží lokálny príjem pozastavený, stop vyžaduje nové play alebo prevzatie podľa MR-05. Nová relácia alebo zmena vstupu mastera môže v oboch prípadoch prevziať satelit podľa MR-05.
- **TRN-04:** Skupinové mute na masteri je samostatný stav vlastnej skupiny, nezávislý od zapnutia centrálnej hlasitosti. Každé zariadenie má aj vlastné lokálne mute. Hudobný výstup člena je stíšený, ak platí lokálne alebo skupinové mute; skupinové unmute nesmie zrušiť lokálne mute ani meniť hlasitosť či referencie. Nový člen vlastného prehrávania prevezme aktuálne skupinové mute.
- **TRN-05:** WebUI a API rozlišujú skupinové a lokálne mute vrátane lokálneho mute mastera. Skupinové príkazy neovplyvňujú satelity s iným aktívnym vstupom. Pri Music Assistant sa skupina ovláda v MA; nextoomio poskytuje lokálnu hlasitosť/mute a lokálne zastavenie príjmu bez zastavenia ostatných endpointov. Ďalšie transportné príkazy MA sa sprístupnia iba po overení ich dosahu.

Akceptačné scenáre:

| Scenár | Očakávané správanie |
| --- | --- |
| Master pozastaví lokálny súbor a následne ho obnoví. | Riadená skupina sa pozastaví a pokračuje synchronizovane; satelit s MA zostane bez zmeny. |
| Satelit pozastaví príjem zo vstupu Master a neskôr stlačí play. | Ostatní pokračujú bez zmeny; satelit sa pripojí na aktuálnu pozíciu, neprehrá zameškaný úsek. |
| Jeden satelit má lokálne mute; master zapne a následne vypne skupinové mute. | Po skupinovom unmute zostane daný satelit lokálne stíšený a ostatní pokračujú podľa svojich lokálnych nastavení. |
| Master zastaví rádio a satelit medzitým prejde na Bluetooth. | Satelit s Bluetooth sa nezastaví ani neprevezme. Ostatní zostanú v tichu, až kým nezačne nové prehrávanie alebo príde explicitné play. |

### 5.3 Výpadky, obnovenie a štart

- **REC-01:** Po reštarte sa obnoví konfigurácia, hlasitosť, referencie a lokálne/skupinové mute, ale predvolene sa hudba automaticky nespustí. Výnimkou môže byť používateľom výslovne nakonfigurovaná štartovacia akcia. Prijímače môžu prijať nový začiatok prehrávania. Uložený vstup Master sa aktivuje explicitným play alebo novým prevzatím masterom podľa MR-05.
- **REC-02:** Pri strate mastera alebo audio spojenia satelit zahodí neplatné/staré audio a čaká v tichu. Ak používateľ zostal na vstupe Master a príjem sám nepozastavil alebo nezastavil, po obnovení spojenia sa automaticky pripojí k aktuálnej pozícii tej istej relácie. Ide o obnovenie prerušenej relácie, nie návrat k ukončenému zdroju podľa SEL-05.
- **REC-03:** Pokusy o pripojenie majú odstupy 1, 2, 4, 8 a následne najviac 10 sekúnd. Prepnutie na iný vstup alebo lokálny stop zruší automatický návrat. Výpadok jedného satelitu nezastaví mastera ani ostatné satelity.
- **REC-04:** Zariadenie odmietne ako mastera samo seba aj zistený cyklus medzi zariadeniami. Prototyp povoľuje jedinú úroveň master → satelity; satelit nesmie prijatý vstup Master distribuovať ďalšej skupine.

## 6. Music Assistant

- **MA-01:** Zariadenie musí byť použiteľné ako prehrávací endpoint Music Assistant.
- **MA-02:** Pri prehrávaní cez Music Assistant je nadradeným riadením Music Assistant; vlastný master projektu sa pre túto cestu nepoužíva.
- **MA-03:** V režime Music Assistant má správu skupín na starosti Music Assistant, bez ďalšej vnorenej vrstvy vlastného mastera.
- **MA-04:** V samostatnej prevádzke zostáva dostupný vlastný multiroom podľa kapitoly 5.
- **MA-05:** Music Assistant je z pohľadu zariadenia ďalší vstup. Lokálne prevzatie iným vstupom nesmie samo prepínať ostatné endpointy Music Assistant.
- **MA-06:** Zvuk prijatý z Music Assistant sa nesmie distribuovať vlastným multiroom. Každý endpoint skupiny Music Assistant prijíma prehrávanie cez cestu riadenú Music Assistant, bez redistribúcie cez vlastný master projektu.
- **MA-07:** Prevzatie vlastného mastera vstupom Music Assistant zastaví jeho vlastnú distribúciu. Slaves používajúce vstup Master zostávajú nakonfigurované a čakajú v tichu. Slaves s aktívnym samostatným vstupom alebo Music Assistant pokračujú bez zmeny. Music Assistant môže slaves prevziať samostatne ako svoje endpointy.
- **MA-08:** Nový začiatok prehrávania samostatného vstupu adresovaný priamo zariadeniu môže prevziať jeho prehrávanie z Music Assistant. Nový začiatok prehrávania z Music Assistant adresovaný tomuto zariadeniu ho môže opäť prevziať. Samotné pokračovanie už existujúceho streamu sa nepovažuje za nový začiatok prehrávania. Ostatné endpointy Music Assistant sa tým automaticky neprepínajú.

Konkrétny endpoint protokol, discovery a oznamovanie lokálneho prevzatia výstupu Music Assistant sa navrhnú neskôr.

### 6.1 Akceptačné scenáre vlastníctva prehrávania

| Scenár | Očakávané správanie |
| --- | --- |
| Master prehráva rádio a dva slaves používajú vstup Master. Music Assistant začne prehrávať iba na masteri. | Master prehráva Music Assistant a zastaví vlastnú distribúciu. Oba slaves čakajú v tichu; konfigurácia skupiny zostane uložená. |
| Music Assistant začne prehrávať na masteri aj dvoch slaves ako na svojej skupine. | Každý endpoint prehráva cez cestu riadenú Music Assistant. Vlastný multiroom neprenáša tento zvuk. |
| Slave počas vlastného multiroom začne prijímať Bluetooth. | Iba tento slave prejde na Bluetooth; master a ostatné slaves pokračujú. |
| Master následne prepne rádio na AirPlay; jeden slave prehráva Bluetooth a druhý Music Assistant. | Master a slave s Bluetooth prejdú na vlastný multiroom s AirPlay. Slave prehrávajúci Music Assistant pokračuje bez zmeny. |
| Zariadenie prehrávajúce Music Assistant začne prijímať nový samostatný vstup; pôvodný stream Music Assistant ďalej prichádza. | Nový vstup prevezme zariadenie. Pokračujúci pôvodný stream ho neprevezme späť; nový začiatok prehrávania adresovaný z Music Assistant ho prevziať môže. Ostatné endpointy Music Assistant pokračujú bez zmeny. |
| Music Assistant ukončí prehrávanie na zariadení, ktoré predtým prehrávalo iný zdroj. | Zariadenie zostane nečinné bez automatického návratu k predchádzajúcemu zdroju. |
| Master po ukončení Music Assistant začne prehrávať rádio. | Obnoví vlastnú distribúciu a prevezme nakonfigurované slaves podľa MR-05; zariadenia stále riadené Music Assistant neovplyvní. |

## 7. Home Assistant Voice

- **VOICE-01:** Po pripojení USB mikrofónu môže zariadenie fungovať ako hlasový satelit pre Home Assistant Voice.
- **VOICE-02:** Pri zachytení aktivačného slova zariadenie dočasne stíši hudbu iba na svojom lokálnom výstupe. Hudobné prehrávanie pokračuje bez pozastavenia; vo vlastnom multiroom aj Music Assistant sa zachová synchronizácia s ostatnými endpointmi.
- **VOICE-03:** Zariadenie musí umožniť zachytiť hlas a prehrať odpoveď hlasového asistenta iba na zariadení, na ktorom bola interakcia aktivovaná. Odpoveď sa neprenáša do vlastnej skupiny ani na ostatné endpointy Music Assistant.
- **VOICE-04:** Hlasová interakcia je dočasná lokálna audio vrstva nad hudobným prehrávaním. Samotné zachytenie aktivačného slova, zachytenie hlasu a prehratie odpovede nemenia aktívny hudobný vstup, vlastníka prehrávania ani členstvo a riadenie skupín. Hlasová odpoveď nepodlieha pravidlu „posledný prichádzajúci vstup vyhráva“.
- **VOICE-05:** Na masteri sa dočasné lokálne stíšenie hudby a pridanie hlasovej odpovede vykonajú až za odbočkou do vlastnej multiroom distribúcie. Slaves naďalej prijímajú pôvodný hudobný stream bez tohto stíšenia a bez hlasovej odpovede. Rovnaká lokálna izolácia platí pri prehrávaní cez Music Assistant.
- **VOICE-06:** Dočasné stíšenie hudby sa odstráni po skončení alebo zrušení interakcie, pri chybe aj po uplynutí časového limitu. Chyba alebo chýbajúca odpoveď asistenta nesmú ponechať hudbu natrvalo stíšenú.
- **VOICE-07:** Dočasné stíšenie sa aplikuje oddelene od používateľského nastavenia hudobnej hlasitosti a mute. Po jeho odstránení sa použijú aktuálne nastavenia; zmeny vykonané počas interakcie sa nesmú prepísať hodnotami spred interakcie.
- **VOICE-08:** Skončenie hlasovej interakcie nesmie spustiť zastavenú hudbu ani obnoviť predchádzajúci vstup alebo vlastníka prehrávania. Ak počas interakcie nastane zmena hudobného prehrávania, po odstránení dočasného stíšenia zostane platný aktuálny stav.

Výslovný používateľský príkaz na ovládanie hudby sprostredkovaný hlasovým asistentom sa vykoná podľa bežných pravidiel prehrávania a skupín. Vyššie uvedené obmedzenia sa týkajú samotnej hlasovej interakcie a jej audio vrstvy.

Predvolené dočasné stíšenie hudby je −20 dB, s nábehom a návratom približne 200 ms; uplatní sa iba lokálne. Celá interakcia vrátane odpovede má v prototype pevný limit 60 sekúnd, po ktorom sa ukončí a dočasné stíšenie sa odstráni. Detekčný mechanizmus, prenos hlasu, výber mikrofónu a potlačenie ozveny sa vyberú pri technickom návrhu. Rozpoznávanie reči a generovanie odpovedí nemusia bežať priamo na RPi.

### 7.1 Akceptačné scenáre hlasovej interakcie

| Scenár | Očakávané správanie |
| --- | --- |
| Master a dva slaves prehrávajú rádio; hlasová interakcia sa aktivuje na masteri. | Iba master lokálne stíši hudbu a prehrá odpoveď. Slaves pokračujú s pôvodnou hudbou bez hlasovej odpovede. Po interakcii master odstráni stíšenie a zostáva synchronizovaný. |
| Hlasová interakcia sa aktivuje na slave prijímajúcom vstup Master. | Iba tento slave lokálne stíši hudbu a prehrá odpoveď; vstup Master zostane aktívny. Master a ostatné slaves pokračujú bez zmeny. |
| Zariadenie je členom skupiny Music Assistant a aktivuje sa na ňom hlasová interakcia. | Lokálna hlasová vrstva neprevezme vlastníctvo hudobného prehrávania, nezmení skupinu ani neovplyvní ostatné endpointy. Hudobné prehrávanie pokračuje synchronizovane. |
| Používateľ počas interakcie zmení hudobnú hlasitosť alebo mute. | Po skončení interakcie platí nové nastavenie; neobnoví sa pôvodná hlasitosť ani pôvodný mute. |
| Používateľ počas interakcie zastaví hudbu. | Dokončenie hlasovej odpovede ani odstránenie dočasného stíšenia hudbu znovu nespustí. |
| Počas interakcie začne nový vstup a podľa pravidiel arbitráže prevezme hudobné prehrávanie. | Po skončení interakcie zostane aktívny nový vstup a jeho vlastník; pôvodný zdroj sa neobnoví. |
| Interakcia sa zruší, asistent zlyhá alebo neodpovie v stanovenom časovom limite. | Dočasné stíšenie sa odstráni a platí aktuálny stav hudobného prehrávania a používateľských nastavení. |

## 8. Ovládanie a stav

| ID | Rozhranie | Rozsah |
| --- | --- | --- |
| CTL-01 | Lokálne API | Programové ovládanie zariadenia a čítanie stavu. |
| CTL-02 | MQTT | Ovládanie, publikovanie stavov a udalostí. |
| CTL-03 | Matter | Maximálny technicky realizovateľný rozsah štandardného ovládania a hlásenia stavu; bez požiadavky na certifikáciu. |
| CTL-04 | Základné webUI | Lokálne používateľské ovládanie a konfigurácia. |

### 8.1 Matter

Sprístupniť všetky funkcie, ktoré možno korektne reprezentovať štandardnými Matter clustrami. Konkrétny rozsah sa má určiť mapovaním funkcií a overením v cieľových ekosystémoch; bez overenia sa nesľubuje rovnaký rozsah v každom controlleri.

Funkcie, ktoré Matter nepokryje, zostanú dostupné cez lokálne API, MQTT a webUI. Zariadenie má byť ovládaným zariadením, nie Matter centrálou alebo bridge pre ďalšie zariadenia.

### 8.2 Základné webUI

Požadované je základné webové rozhranie. Ako jeho pracovné minimum sa navrhuje:

- aktuálny vstup, stav prehrávania a dostupné metadáta;
- play/pause/stop podľa možností vstupu, hlasitosť a mute;
- výber vstupu, rádia a lokálnych/NAS súborov;
- konfigurácia vstupu Master a zobrazenie multiroom stavu;
- zapnutie/vypnutie centrálneho riadenia hlasitosti a nastavenie referenčnej hlasitosti jednotlivých satelitov;
- nastavenie siete, audia, MQTT a nadväzujúcich akcií;
- stav zariadenia, manuálny upload aktualizačného image, priebeh aktualizácie a základná diagnostika.

Tento zoznam konkretizuje základné webUI; presná podoba obrazoviek zostáva predmetom návrhu.

## 9. Udalosti a nadväzujúce akcie

- **ACT-01:** K udalosti možno priradiť najviac **10 nadväzujúcich akcií**.
- **ACT-02:** Podporované budú lokálne akcie, webové požiadavky a MQTT správy, podobne ako Shelly Actions.
- **ACT-03:** Pokryť bežné udalosti zariadenia a prehrávania.

Počiatočný katalóg udalostí: zariadenie pripravené, začiatok/pozastavenie/ukončenie prehrávania, zmena vstupu, hlasitosti alebo mute, master dostupný/nedostupný a úspešná/neúspešná aktualizácia. MQTT témy a presné payloady sa určia technickým návrhom. Udalosť zmeny vzniká iba pri skutočnej zmene hodnoty, nie pri opakovanom zápise rovnakej hodnoty.

Príklad použitia: začiatok prehrávania vyvolá HTTP alebo MQTT akciu na zapnutie externého zosilňovača.

Od dodania akcií vo v0.0.4 platia tieto pravidlá:

- Akcie jednej udalosti sa vykonajú postupne v uloženom poradí. Každá môže mať oneskorenie 0–60 sekúnd, timeout vykonania je 3 sekundy. Vykonávanie neblokuje audio ani ovládanie.
- HTTP akcie podporujú GET/POST s pevne nastavenou URL, hlavičkami a telom. MQTT akcie publikujú na nastavenú tému s QoS 0 bez retain. Lokálne akcie používajú pevný zoznam: výber vstupu, play/pause/stop, nastavenie hlasitosti a mute; ľubovoľné shell príkazy nie sú súčasťou prototypu.
- Pri chybe sa zaznamená výsledok a pokračuje ďalšou akciou. Automatické opakovanie, podmienky, skripty a trvalé doručovanie po reštarte sa v prototype neimplementujú.
- Zmena vyvolaná lokálnou akciou sa publikuje ako stav/udalosť, ale nespustí ďalší reťazec akcií. Súbežné vykonávanie toho istého pravidla je zakázané. Pravidlo, ktoré sa pokúsi spustiť viac než 20-krát za 10 sekúnd, sa deaktivuje s diagnostickou chybou aj pri spätnej väzbe cez HTTP/MQTT.
- Fronta má najviac 20 čakajúcich udalostí pre akcie; prekročenie sa zaznamená a ďalšie udalosti sa pre účely akcií zahodia. Publikovanie aktuálneho stavu a hudobné prehrávanie pokračujú. Pri ukončení procesu sa čakajúce akcie zrušia.

## 10. Bezpečnosť, dáta a aktualizácie

Pre sériu v0.0.1–v0.0.5 v lokálnej sieti sú záväzné tieto minimálne požiadavky. Každá ochrana musí byť dodaná najneskôr spolu s funkciou, ktorej sa týka; napríklad autentifikácia API vo v0.0.1, prístupový kľúč multiroom vo v0.0.2 a MQTT TLS vo v0.0.4:

- jeden administrátorský účet nastavený pri prvom spustení, bez univerzálneho predvoleného hesla; prístup pred dokončením nastavenia slúži iba na prvotné nastavenie;
- autentifikácia webUI a lokálneho API vrátane uploadu image; ochrana proti CSRF pri relácii v prehliadači a overovanie vstupných dát;
- HTTPS pre webUI/API s lokálnym certifikátom; podpora MQTT TLS s overovaním certifikátov; nešifrované MQTT možno výslovne nastaviť pre lokálny broker;
- explicitné párovanie Bluetooth a zdieľaný náhodný prístupový kľúč vlastnej multiroom skupiny, ktorý sa nastaví na masteri a jeho satelitoch;
- heslá administrátora uložené ako solené hashe, ostatné tajomstvá v perzistentnom úložisku s prístupom iba potrebných služieb a ich vynechanie z bežných logov;
- A/B aktualizácia a automatický návrat pri neúspešnom štarte alebo kontrole funkčnosti;
- zachovanie konfigurácie a používateľských dát pri aktualizácii aj návrate na starší slot;
- minimálne oprávnenia služieb a obmedzenie nepotrebných sieťových služieb;
- kontrolované vykonávanie lokálnych akcií a ochrana pred slučkami;
- bezpečné prvotné nastavenie, záloha a obnova konfigurácie.

Prototyp sa prevádzkuje v lokálnej sieti bez priameho vystavenia na internet. Konkrétna implementácia autentifikácie, certifikátov, partition layout a aktualizačný nástroj sa vyberú technickým návrhom. Zachovanie dát pri rollbacku sa overí vrátane kompatibility ich schémy; nová verzia nesmie nezvratne prepísať konfiguráciu potrebnú pre návrat na starý slot.

### 10.1 Manuálne aktualizácie série v0.0.1–v0.0.5

- **UPD-01:** Používateľ spustí aktualizáciu manuálnym uploadom systémového image cez lokálne webUI. Aktualizácia nevyžaduje internetové pripojenie zariadenia.
- **UPD-02:** Zariadenie nevyhľadáva, nesťahuje ani automaticky neinštaluje online aktualizácie. Jediným aktualizačným vstupom je manuálne nahraný image.
- **UPD-03:** V sérii v0.0.1–v0.0.5 sa neimplementuje podpisovanie ani overovanie kryptografických podpisov aktualizačných image. Podpis image nie je podmienkou jeho prijatia.
- **UPD-04:** Manuálne nahraný image sa inštaluje cez A/B mechanizmus podľa SYS-05. Pred aktiváciou sa overí formát, úplnosť image a kompatibilita s cieľovým zariadením; tieto kontroly nie sú overením podpisu ani pôvodu image. Konkrétny formát image a validačný postup sa určia v technickom návrhu.
- **UPD-05:** Upload sa najprv uloží a overí mimo aktívneho systémového slotu. Neplatný/neúplný image alebo nedostatok miesta nezmenia bootovací slot. Po explicitnom spustení inštalácie sa prehrávanie zastaví a image sa zapíše do neaktívneho slotu; samotný upload prehrávanie nezastavuje.
- **UPD-06:** Nový slot sa označí ako funkčný až po úspešnom štarte lokálneho riadenia, API a nakonfigurovaného audio výstupu. Každý skúšobný štart má na dosiahnutie funkčného stavu 120 sekúnd; prekročenie limitu alebo pád znamenajú neúspešný pokus. Povolené sú najviac dva pokusy, potom sa pri ďalšom štarte použije predchádzajúci slot. Nedostupnosť internetu, NAS, MA alebo HA sama nesmie vyvolať rollback. Prototyp musí zabezpečiť reštart pri uviaznutí skúšobného štartu.

## 11. Open source a licencia

Projekt bude zverejnený ako open source. Predbežnou licenciou vlastného kódu je **Apache License 2.0 (Apache-2.0)**; konečné potvrdenie a kompatibilita so zvolenými komponentmi sa preveria pred publikovaním.

Označenie „Apache 3“ z diskusie bolo opravené na Apache-2.0. Externé komponenty si ponechávajú svoje licencie; celý Yocto obraz nemožno bez posúdenia označiť jedinou licenciou vlastnej aplikácie.

## 12. Mimo aktuálneho rozsahu

- Zigbee: pôvodne uvedené omylom namiesto MQTT.
- Samostatná priama integrácia prehrávača do Home Assistant: zatiaľ sa nepožaduje; Music Assistant a HA Voice sú samostatne požadované funkcie.
- Bluetooth audio vysielač.
- Certifikácia Matter.
- Viac masterov v jednej vlastnej skupine a automatické prevzatie úlohy pri strate mastera.
- Samostatné priame integrácie katalógov Apple Music, YouTube Music a SoundCloud.
- Online vyhľadávanie, sťahovanie a automatická inštalácia aktualizácií v sérii v0.0.1–v0.0.5.
- Podpisovanie a overovanie kryptografických podpisov aktualizačných image v sérii v0.0.1–v0.0.5.

## 13. Dodanie série prototypov v0.0.1–v0.0.5

### 13.1 Rozdelenie do verzií

Úplný plán funkcií, závislostí, podmienok vydania a pokrytie všetkých ID požiadaviek sú v [ROADMAP.md](ROADMAP.md).

| Verzia | Nový funkčný rozsah |
| --- | --- |
| v0.0.1 | Samostatný Yocto prehrávač: lokálne súbory, rádiá, API, webUI, bezpečné prvotné nastavenie, manuálne A/B aktualizácie a záloha/obnova konfigurácie. |
| v0.0.2 | NAS, konfigurácia Wi-Fi a vlastný multiroom vrátane centrálnej hlasitosti, referencií satelitov, skupinového ovládania a zotavenia po výpadku. |
| v0.0.3 | AirPlay 2, Spotify Connect, Bluetooth prijímač a Music Assistant vrátane úplnej arbitráže vstupov a izolácie skupín. |
| v0.0.4 | MQTT, udalosti, najviac 10 nadväzujúcich akcií a Home Assistant Voice s lokálnou hlasovou vrstvou. |
| v0.0.5 | Matter v overenom realizovateľnom rozsahu, dokončenie podpory RPi 3+ a bežne rozšírených HiFiBerry kariet, kompletné webUI a integračné overenie celého dohodnutého rozsahu. |

Verzie sú kumulatívne. Presun funkcie vyžaduje aktualizáciu roadmapy aj matice požiadaviek; nesmie viesť k tichému vypusteniu funkcie z v0.0.5. Funkcia sa označí ako dodaná až po úspešnom overení. Ide o plán dodania, nie potvrdenie existujúcej implementácie.

### 13.2 Počiatočné akceptačné ciele

Číselné hodnoty sú ciele na prvé meranie, nie už preukázané vlastnosti. Výsledok sa uvedie spolu s hardvérom, zapojením a verziou image; nesplnenie sa nesmie označiť ako úspešný test.

| Oblasť | Cieľ a spôsob overenia od verzie dodania príslušnej funkcie |
| --- | --- |
| Štart | Lokálne webUI/API dostupné do 90 sekúnd od zapnutia na referenčnom zapojení s Ethernet/DHCP. Bez automatického spustenia hudby pri predvolenej konfigurácii. |
| Multiroom rozsah | Najviac 4 zariadenia v skupine: 1 master a 3 satelity. Povinný prvý test na 2 zariadeniach, následne test na všetkých 4. |
| Synchronizácia | Rozdiel začiatku rovnakého audio impulzu medzi elektrickými výstupmi najviac 2 ms po ustálení; spoločné meranie minimálne dvoch výstupov a kontrola driftu počas 30 minút. Základné meranie na káblovej sieti; Wi-Fi sa vyhodnotí samostatne. |
| Pripojenie k skupine | Do 5 sekúnd od úspešného spojenia prejde satelit na aktuálnu pozíciu a dosiahne cieľ synchronizácie. |
| Výpadok | Po strate streamu najneskôr do 2 sekúnd ticho bez opakovania starého buffera; po obnovení stabilnej siete a dostupného mastera obnovenie aktívnej relácie do 15 sekúnd. |
| Ovládanie | Lokálne API potvrdí prijatie bežného príkazu do 300 ms; pri dlhšej operácii vracia priebeh/stav. Prehrávanie lokálneho súboru alebo priameho rádia začne do 5 sekúnd pri dostupnom zdroji. |
| Základná stabilita | Aspoň 2 hodiny nepretržitého prehrávania na referenčnom zapojení bez pádu služby alebo samovoľného prepnutia vstupu. |
| Hlasová vrstva | Scenáre z kapitoly 7.1 vrátane chyby/timeoutu; žiadna hlasová odpoveď ani dočasné stíšenie sa neobjavia na inom endpointe. |
| Aktualizácia | Odmietnutie poškodeného a nesprávneho image; úspešný upload/inštalácia; overený rollback nefunkčného image aj zachovanie konfigurácie a referencií. |
| Izolácia ovládania | Lokálny zásah na satelite a zmena skupinovej hlasitosti/mute neovplyvnia endpointy prehrávajúce cez MA. Overiť aj oneskorené udalosti starej relácie. |

### 13.3 Technické výstupy potrebné pri implementácii

- Vybrané audio a integračné komponenty, ich verzie, licencie a výsledky overenia; výsledný image sa neposudzuje ako jediný balík s licenciou vlastného kódu.
- Popis audio pipeline vrátane odbočky do multiroom pred lokálnou hlasitosťou/mute a hlasovou vrstvou, stavov relácie a rušenia starých požiadaviek.
- Konkrétne API, MQTT témy/payloady, autentifikácia a chybové odpovede. WebUI a MQTT používajú rovnaký model príkazov a stavu ako lokálne API.
- Yocto vrstvy, formát aktualizačného image, partition layout, kontrola skúšobného bootu, konfigurácia kompatibilná s rollbackom a postup zálohy/obnovy.
- Výsledky akceptačných scenárov a zoznam konkrétnych chýb/obmedzení pre nasledujúce vydanie. Pri v0.0.5 nesmie nevyriešené obmedzenie nahradiť dohodnutú povinnú funkciu. Tento dokument je návrh; bez výsledkov nepredstavuje hotovú implementáciu.

## 14. Referenčné podklady

Externé odkazy sú podkladmi pre následný technický návrh, nie záväzným výberom komponentov:

- [Apache – licencie](https://www.apache.org/licenses/)
- [Apache – licenčné FAQ](https://www.apache.org/foundation/license-faq.html)
- [Shairport Sync – AirPlay prijímač](https://github.com/mikebrady/shairport-sync)
- [Shairport Sync – konfigurácia a kompatibilitný režim](https://github.com/mikebrady/shairport-sync/blob/master/scripts/shairport-sync.conf)
- [librespot – Spotify klient](https://github.com/librespot-org/librespot)
- [Music Assistant – vývojárska dokumentácia](https://developers.music-assistant.io/)
