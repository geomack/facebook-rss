# Facebook и Telegram в RSS

- [Facebook](https://geomack.github.io/facebook-rss/feed.xml)
- [Telegram](https://geomack.github.io/facebook-rss/telegram.xml)

## Добавить или убрать источник

1. Открой [Build Facebook and Telegram RSS](https://github.com/geomack/facebook-rss/actions/workflows/rss.yml) и нажми **Run workflow**. Ветка должна быть **main**.
2. Выбери **add** или **remove** и ленту **telegram** или **facebook**.
3. В **source** вставь `@username` или ссылку `t.me` для Telegram, прямую ссылку на страницу или профиль для Facebook. Для нового источника можно задать подпись в **name**.
4. Нажми **Run workflow**. Результат появится по той же ссылке RSS после успешной сборки и публикации GitHub Pages.

**remove** отключает источник и убирает его записи из текущей версии RSS. Некоторые ридеры сохраняют у себя уже загруженные записи. **add** с тем же адресом снова включает источник. **refresh** пересобирает обе ленты.

Списки источников также можно менять в [telegram.json](telegram.json) и [pages.json](pages.json). Коммит в `main` запускает сборку. По расписанию сборка запускается каждые два часа. Сборщики читают публично доступные страницы. Facebook может показать только часть постов. Дата RSS показывает, когда сборщик впервые увидел запись.
