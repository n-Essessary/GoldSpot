# WoW Gold Market — Frontend Structure

```
Front_1.0/
├── index.html
├── vite.config.js          # proxy: /api → http://localhost:8000 (rewrite без префикса)
├── package.json
│
└── src/
    ├── main.jsx
    ├── App.jsx
    ├── App.module.css
    ├── index.css             # токены + reset
    │
    ├── api/
    │   └── offers.js         # fetch('/api/offers?…') → data.offers
    │
    ├── hooks/
    │   └── useOffers.js      # state, fetch, фильтры → сервер, 2 таймера (15 с + 1 с)
    │
    └── components/
        ├── FiltersBar.jsx    # server, faction, limit
        ├── OffersTable.jsx   # все поля Offer + Open при offer_url
        ├── RefreshButton.jsx
        └── StatusBar.jsx
```

Запуск: бэкенд на `:8000`, фронт `npm run dev` → запросы идут на `/api/offers` (прокси на `http://localhost:8000/offers`).
