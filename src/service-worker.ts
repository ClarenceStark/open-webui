/// <reference types="@sveltejs/kit" />

import { build, files, version } from '$service-worker';

const sw = self as ServiceWorkerGlobalScope;
const CACHE = `open-webui-app-${version}`;
const ASSETS = [...build, ...files];
const EXCLUDED_PREFIXES = ['/api', '/ws', '/socket.io', '/health', '/manifest.json'];

sw.addEventListener('install', (event) => {
	event.waitUntil(
		(async () => {
			const cache = await caches.open(CACHE);
			await cache.addAll(ASSETS);
			await sw.skipWaiting();
		})()
	);
});

sw.addEventListener('activate', (event) => {
	event.waitUntil(
		(async () => {
			for (const key of await caches.keys()) {
				if (key !== CACHE) {
					await caches.delete(key);
				}
			}

			await sw.clients.claim();
		})()
	);
});

sw.addEventListener('fetch', (event) => {
	const { request } = event;

	if (request.method !== 'GET') {
		return;
	}

	const url = new URL(request.url);
	if (url.origin !== sw.location.origin) {
		return;
	}

	if (EXCLUDED_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) {
		return;
	}

	event.respondWith(
		(async () => {
			const cache = await caches.open(CACHE);

			if (ASSETS.includes(url.pathname)) {
				const cached = await cache.match(url.pathname);
				if (cached) {
					return cached;
				}
			}

			try {
				const response = await fetch(request);

				if (response instanceof Response && response.ok && request.mode !== 'navigate') {
					cache.put(request, response.clone());
				}

				return response;
			} catch (error) {
				const cached = await cache.match(request);
				if (cached) {
					return cached;
				}

				throw error;
			}
		})()
	);
});
