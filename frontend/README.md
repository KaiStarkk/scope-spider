# Carbon Credit Research Tool - Frontend

This project is a SvelteKit application that serves as the user interface for the Carbon Credit Research Tool. It's built with Svelte 5, TypeScript, and styled with Skeleton and Tailwind CSS.

## Developing

Once you've cloned the project, install dependencies with `npm install` (or `pnpm install` or `yarn`). Then, start a development server:

```sh
npm run dev

# or start the server and open the app in a new browser tab
npm run dev -- --open
```

The frontend will be available at `http://localhost:5173`. It is configured to proxy API requests from `/api` to the backend running on `http://127.0.0.1:8000`.

## Building

To create a production version of your app:

```sh
npm run build
```

You can preview the production build with `npm run preview`.

> To deploy your app, you may need to install an [adapter](https://svelte.dev/docs/kit/adapters) for your target environment.

## Key Technologies

-   **Framework**: Svelte 5 (with Runes) & SvelteKit
-   **UI Library**: Skeleton
-   **Styling**: Tailwind CSS
-   **Bundler**: Vite
-   **Testing**: Playwright for E2E tests, Vitest for unit tests
