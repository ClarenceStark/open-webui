<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url';
	import Spinner from './Spinner.svelte';

	export let url: string | null = null;
	export let data: ArrayBuffer | Uint8Array | null = null;
	export let className = 'w-full h-[70vh]';

	let containerEl: HTMLDivElement | null = null;
	let loading = true;
	let error = '';
	let pageNumbers: number[] = [];
	let pageCanvases: Array<HTMLCanvasElement | null> = [];
	let destroyed = false;
	let resizeObserver: ResizeObserver | null = null;
	let pdfDoc: any = null;
	let renderToken = 0;
	let resizeRaf = 0;

	const getPdfAssetBaseUrl = () => new URL('/pdfjs/', window.location.origin).toString();

	const canvasRef = (node: HTMLCanvasElement, index: number) => {
		pageCanvases[index] = node;

		return {
			destroy() {
				pageCanvases[index] = null;
			}
		};
	};

	const waitForCanvasRefs = async () => {
		for (let attempt = 0; attempt < 20; attempt++) {
			if (pageCanvases.length === pageNumbers.length && pageCanvases.every(Boolean)) {
				return true;
			}
			await tick();
		}

		return false;
	};

	const destroyPdfDoc = async () => {
		if (!pdfDoc) return;

		try {
			await pdfDoc.destroy();
		} catch (error) {
			console.warn('Failed to destroy PDF document:', error);
		} finally {
			pdfDoc = null;
		}
	};

	const renderPages = async (token: number) => {
		if (!pdfDoc || !containerEl) return;

		const availableWidth = Math.max(containerEl.clientWidth - 32, 320);
		const outputScale = window.devicePixelRatio || 1;

		for (let index = 0; index < pageNumbers.length; index++) {
			if (destroyed || token !== renderToken || !pdfDoc) return;

			const pageNumber = pageNumbers[index];
			const canvas = pageCanvases[index];
			if (!canvas) continue;

			const page = await pdfDoc.getPage(pageNumber);
			const baseViewport = page.getViewport({ scale: 1 });
			const scale = availableWidth / baseViewport.width;
			const viewport = page.getViewport({ scale });
			const context = canvas.getContext('2d', { alpha: false });

			if (!context) continue;

			canvas.width = Math.floor(viewport.width * outputScale);
			canvas.height = Math.floor(viewport.height * outputScale);
			canvas.style.width = `${viewport.width}px`;
			canvas.style.height = `${viewport.height}px`;

			context.setTransform(outputScale, 0, 0, outputScale, 0, 0);
			context.imageSmoothingEnabled = true;
			context.fillStyle = '#ffffff';
			context.fillRect(0, 0, viewport.width, viewport.height);

			await page
				.render({
					canvasContext: context,
					viewport,
					background: 'rgb(255,255,255)'
				})
				.promise;

			page.cleanup();
		}
	};

	const loadPdf = async () => {
		if (!url && !data) return;

		const token = ++renderToken;
		loading = true;
		error = '';
		pageNumbers = [];
		pageCanvases = [];

		try {
			await destroyPdfDoc();

			const pdfjs = await import('pdfjs-dist');
			pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

			let pdfData: ArrayBuffer | Uint8Array;
			if (data) {
				pdfData = data;
			} else {
				const res = await fetch(url!, { credentials: 'include' });
				if (!res.ok) throw new Error(`HTTP ${res.status}`);
				pdfData = await res.arrayBuffer();
			}

			const assetBaseUrl = getPdfAssetBaseUrl();
			pdfDoc = await pdfjs.getDocument({
				data: pdfData,
				cMapUrl: `${assetBaseUrl}cmaps/`,
				cMapPacked: true,
				standardFontDataUrl: `${assetBaseUrl}standard_fonts/`,
				useWorkerFetch: false
			}).promise;

			if (destroyed || token !== renderToken) {
				await destroyPdfDoc();
				return;
			}

			pageNumbers = Array.from({ length: pdfDoc.numPages }, (_, index) => index + 1);
			pageCanvases = Array.from({ length: pdfDoc.numPages }, () => null);

			await tick();

			const canvasesReady = await waitForCanvasRefs();
			if (!canvasesReady || destroyed || token !== renderToken) {
				return;
			}

			await renderPages(token);

			if (destroyed || token !== renderToken) return;
		} catch (e) {
			console.error('PDF preview error:', e);
			if (destroyed || token !== renderToken) return;
			error = 'Failed to load PDF.';
		} finally {
			if (!destroyed && token === renderToken) {
				loading = false;
			}
		}
	};

	const rerenderOnResize = () => {
		if (!pdfDoc || loading) return;

		if (resizeRaf) {
			cancelAnimationFrame(resizeRaf);
		}

		const token = ++renderToken;
		resizeRaf = requestAnimationFrame(async () => {
			resizeRaf = 0;
			try {
				await renderPages(token);
			} catch (error) {
				console.error('PDF rerender error:', error);
			}
		});
	};

	export const resetView = () => {};

	onMount(() => {
		loadPdf();

		if (typeof ResizeObserver !== 'undefined') {
			resizeObserver = new ResizeObserver(() => {
				rerenderOnResize();
			});

			if (containerEl) {
				resizeObserver.observe(containerEl);
			}
		}
	});

	onDestroy(() => {
		destroyed = true;
		if (resizeRaf) {
			cancelAnimationFrame(resizeRaf);
		}
		resizeObserver?.disconnect();
		void destroyPdfDoc();
	});
</script>

<div bind:this={containerEl} class={`relative ${className}`}>
	{#if loading}
		<div class="absolute inset-0 z-10 flex items-center justify-center">
			<Spinner className="size-5" />
		</div>
	{:else if error}
		<div class="absolute inset-0 flex items-center justify-center text-sm text-red-500">
			{error}
		</div>
	{/if}

	<div
		class="h-full overflow-y-auto rounded-lg bg-gray-100 px-4 py-4 dark:bg-gray-900"
		class:opacity-0={loading && !error}
	>
		<div class="mx-auto flex max-w-4xl flex-col items-center gap-4">
			{#each pageNumbers as pageNumber, index}
				<div class="w-full rounded-xl bg-white p-3 shadow-sm ring-1 ring-black/5 dark:bg-white">
					<div class="mb-2 text-center text-xs font-medium uppercase tracking-wide text-gray-400">
						Page {pageNumber}
					</div>
					<canvas use:canvasRef={index} class="mx-auto block max-w-full rounded-sm"></canvas>
				</div>
			{/each}
		</div>
	</div>
</div>
