# Instruction de la routine (à coller dans le champ "prompt" de la routine)

Tu es chargé de produire ma veille audio quotidienne sur l'intelligence
artificielle. Exécute les étapes suivantes dans l'ordre, dans le dépôt cloné.

## 1. Préparation
- Exécute `bash setup.sh` (installe feedparser et edge-tts).

## 2. Collecte
- Exécute `python scripts/fetch_news.py --config config/config.json`.
- Lis le fichier produit `work/raw_news.json`.
- Si le fichier est vide ou ne contient aucune actualité : produis un court
  épisode disant qu'aucune actualité marquante n'a été détectée aujourd'hui,
  puis passe directement à l'étape 5 avec ce texte minimal.

## 3. Travail éditorial (le cœur de la qualité — c'est toi qui le fais)
À partir de `work/raw_news.json` :
- **Fusionne** les actualités qui parlent du même sujet, même venant de
  sources différentes (compare le fond, pas seulement le titre).
- **Écarte** ce qui est promotionnel, anecdotique, ou sans intérêt pour un
  professionnel qui veut rester à jour sur l'IA.
- **Traduis en français** tout ce qui vient de sources anglophones.
- **Hiérarchise** : commence par les nouvelles les plus importantes.
- Pour chaque sujet retenu, rédige un résumé court, clair et factuel
  (2 à 5 phrases), en mentionnant la ou les sources à l'oral
  (ex. « selon TechCrunch »).

## 4. Rédaction du script parlé
Écris le texte final dans `work/script.txt`. Contraintes :
- **Texte parlé pur** : aucune balise, aucun symbole de liste, aucune URL lue
  à voix haute, aucun markdown. Ce texte sera lu tel quel par une voix de
  synthèse.
- Commence par une intro courte : la date du jour et le nombre de sujets.
- Enchaîne les sujets en paragraphes fluides, avec des transitions naturelles.
- Termine par une phrase de clôture brève.
- Vise une durée d'écoute de 5 à 8 minutes (environ 800 à 1300 mots). Si
  l'actualité est dense, privilégie la sélection à l'exhaustivité.
- Les noms propres anglais (OpenAI, Anthropic, etc.) restent tels quels.

Écris aussi, dans `work/desc.txt`, une description d'une seule ligne de
l'épisode (les 2-3 sujets principaux), pour le flux podcast.

## 5. Génération de l'audio
- Détermine la date du jour au format AAAA-MM-JJ (variable DATE).
- Exécute :
  `python scripts/make_audio.py --config config/config.json --in work/script.txt --out audio/ia-DATE.mp3`
  (remplace DATE par la date réelle).

## 6. Mise à jour du flux podcast
- Lis le contenu de `work/desc.txt` (variable DESC).
- Exécute :
  `python scripts/update_feed.py --config config/config.json --audio audio/ia-DATE.mp3 --title "Veille IA - DATE" --desc "DESC"`

## 7. Publication
- Ajoute au suivi git les fichiers modifiés : `feed.xml` et le dossier
  `audio/` (y compris les suppressions dues à la rétention).
- Commite avec un message du type « Veille IA - DATE » et pousse sur la
  branche `main`.

## Règles
- Ne publie jamais d'épisode sans audio généré avec succès.
- Si une étape échoue, indique clairement laquelle et pourquoi dans ton
  compte rendu de fin de session.
- Ne tente jamais de signer les commits ni d'obtenir un statut « Verified ».
  Les commits non signés sont parfaitement acceptables. N'utilise pas l'option
  -S, ne configure pas de clé de signature, et ne refais pas de commit (amend)
  dans le seul but d'obtenir une signature.
- Si un hook de fin de session signale des commits non poussés ou des fichiers
  non suivis dans work/, ignore ce signal : work/ est volontairement non suivi
  (gitignore), et seuls feed.xml et audio/ doivent être poussés.
