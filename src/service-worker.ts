/// <reference no-default-lib="true" />
/// <reference lib="esnext" />
/// <reference lib="webworker" />

// This worker intentionally does not cache or intercept requests. Its only purpose is to replace
// the legacy cache-first worker, clear that worker's caches, reload controlled pages once, and then
// unregister itself. New pages do not register it because kit.serviceWorker.register is disabled.

const worker = globalThis.self as unknown as ServiceWorkerGlobalScope;
const LEGACY_CACHE_PREFIX = 'open-webui-app-';

worker.addEventListener('install', (event) => {
	event.waitUntil(worker.skipWaiting());
});

worker.addEventListener('activate', (event) => {
	event.waitUntil(
		(async () => {
			const cacheKeys = await caches.keys();
			await Promise.all(
				cacheKeys
					.filter((key) => key.startsWith(LEGACY_CACHE_PREFIX))
					.map((key) => caches.delete(key))
			);

			await worker.clients.claim();
			const controlledClients = await worker.clients.matchAll({
				type: 'window',
				includeUncontrolled: true
			});

			await worker.registration.unregister();
			await Promise.all(
				controlledClients.map(async (client) => {
					if ('navigate' in client) {
						try {
							await (client as WindowClient).navigate(client.url);
						} catch {
							// Older Safari versions expose navigate() but throw NotSupportedError.
							// The cache is already cleared and this worker is pass-through, so the
							// client's next manual reload will still recover from the legacy shell.
						}
					}
				})
			);
		})()
	);
});
