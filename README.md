# tenway-content-pipeline

Контент-конвейер: Claude пишет сценарии → GitHub Actions рендерит вертикальное видео (озвучка Fish Audio, демо-скринкаст Playwright, сборка ffmpeg) → готовый ролик прилетает в Telegram.

## Как это работает

1. Claude (Cowork) кладёт сценарий в `jobs/*.json` со статусом `approved`
2. Push запускает workflow `build-videos`
3. Воркер: озвучка по сегментам → запись скринкаста с таймингом по озвучке → субтитры → mp4 1080×1920
4. Ролик + текст поста отправляются в Telegram-бот, `out/<id>/status.md` коммитится обратно как журнал

## Секреты (Settings → Secrets and variables → Actions)

- `FISH_API_KEY` — ключ Fish Audio
- `TG_BOT_TOKEN` — токен Telegram-бота
- `TG_CHAT_ID` — (опционально) chat_id; если не задан, определяется по последнему сообщению боту

## Формат задания

См. `jobs/2026-08-25-chatgpt-3-fishki.json`: `segments[].text` — текст озвучки и субтитров, `segments[].scene` — что происходит на экране (`title` / `user` / `ai` / `outro`).
