<script lang="ts">
	import type { WorkBook } from 'xlsx';
	import DOMPurify from 'dompurify';

	import { getContext, onMount, tick } from 'svelte';

	import { formatFileSize, getLineCount } from '$lib/utils';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { settings } from '$lib/stores';
	import { getKnowledgeById } from '$lib/apis/knowledge';
	import { getFileById, getFileContentById } from '$lib/apis/files';

	import CodeBlock from '$lib/components/chat/Messages/CodeBlock.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';

	const i18n = getContext('i18n');

	const CONTENT_PREVIEW_LIMIT = 10000;
	let expandedContent = false;

	import Modal from './Modal.svelte';
	import XMark from '../icons/XMark.svelte';
	import Download from '../icons/Download.svelte';
	import Switch from './Switch.svelte';
	import Tooltip from './Tooltip.svelte';
	import dayjs from 'dayjs';
	import Spinner from './Spinner.svelte';
	import PDFViewer from './PDFViewer.svelte';
	import Reset from '../icons/Reset.svelte';

	import panzoom, { type PanZoom } from 'panzoom';

	export let item;
	export let show = false;
	export let edit = false;

	let enableFullContent = false;
	let loading = false;

	let isPDF = false;
	let isAudio = false;
	let isImage = false;
	let isExcel = false;
	let isDocx = false;
	let isPptx = false;
	let isText = false;

	let selectedTab = '';
	let excelWorkbook: WorkBook | null = null;
	let excelSheetNames: string[] = [];
	let selectedSheet = '';
	let excelHtml = '';
	let excelError = '';
	let rowCount = 0;

	// DOCX state
	let docxHtml = '';
	let docxError = '';

	// PPTX state
	let pptxSlides: string[] = [];
	let pptxCurrentSlide = 0;
	let pptxError = '';
	let textContent = '';
	let textContentError = '';

	let pzInstance: PanZoom | null = null;

	const TEXT_EXTS = new Set(['txt', 'text', 'log', 'ini', 'cfg', 'conf', 'env', 'rst']);

	const getFileExt = (name?: string | null) => {
		if (!name) return '';
		const parts = name.toLowerCase().split('.');
		return parts.length > 1 ? parts.pop() ?? '' : '';
	};

	const getContentType = () =>
		item?.meta?.content_type ?? item?.content_type ?? item?.file?.meta?.content_type ?? '';

	const getFileId = () => {
		if (item?.id) return item.id;
		if (typeof item?.url !== 'string' || item.url.length === 0) return null;

		if (!item.url.startsWith('http') && !item.url.startsWith('/')) {
			return item.url.replace(/\/content$/, '');
		}

		try {
			const parsedUrl = item.url.startsWith('http')
				? new URL(item.url)
				: new URL(item.url, window.location.origin);
			const match = parsedUrl.pathname.match(/\/files\/([^/]+)(?:\/content)?$/);
			return match?.[1] ?? null;
		} catch (error) {
			console.error('Error parsing file URL:', error);
			return null;
		}
	};

	const getFileContentUrl = () => {
		if (typeof item?.url === 'string' && item.url.length > 0) {
			if (item.url.startsWith('http') || item.url.startsWith('/')) {
				return item.url;
			}

			return `${WEBUI_API_BASE_URL}/files/${item.url}/content`;
		}

		const fileId = getFileId();
		return fileId ? `${WEBUI_API_BASE_URL}/files/${fileId}/content` : null;
	};

	const getFileDownloadUrl = () => {
		const fileUrl = getFileContentUrl();
		if (!fileUrl) return null;
		return `${fileUrl}${fileUrl.includes('?') ? '&' : '?'}attachment=true`;
	};

	const downloadFile = () => {
		const downloadUrl = getFileDownloadUrl();
		if (!downloadUrl) return;
		window.open(downloadUrl, '_blank');
	};

	const getResolvedTextContent = () => item?.file?.data?.content ?? item?.content ?? textContent ?? '';

	const initImagePanzoom = (node: HTMLElement) => {
		pzInstance = panzoom(node, {
			bounds: true,
			boundsPadding: 0.1,
			zoomSpeed: 0.065
		});
	};

	const resetImageView = () => {
		if (pzInstance) {
			pzInstance.moveTo(0, 0);
			pzInstance.zoomAbs(0, 0, 1);
		}
	};

	$: isPDF =
		getContentType() === 'application/pdf' ||
		(item?.name && item?.name.toLowerCase().endsWith('.pdf'));

	$: isMarkdown =
		getContentType() === 'text/markdown' ||
		(item?.name && item?.name.toLowerCase().endsWith('.md'));

	$: isCode =
		item?.name &&
		(item.name.toLowerCase().endsWith('.py') ||
			item.name.toLowerCase().endsWith('.js') ||
			item.name.toLowerCase().endsWith('.ts') ||
			item.name.toLowerCase().endsWith('.java') ||
			item.name.toLowerCase().endsWith('.html') ||
			item.name.toLowerCase().endsWith('.css') ||
			item.name.toLowerCase().endsWith('.json') ||
			item.name.toLowerCase().endsWith('.cpp') ||
			item.name.toLowerCase().endsWith('.c') ||
			item.name.toLowerCase().endsWith('.h') ||
			item.name.toLowerCase().endsWith('.sh') ||
			item.name.toLowerCase().endsWith('.bash') ||
			item.name.toLowerCase().endsWith('.yaml') ||
			item.name.toLowerCase().endsWith('.yml') ||
			item.name.toLowerCase().endsWith('.xml') ||
			item.name.toLowerCase().endsWith('.sql') ||
			item.name.toLowerCase().endsWith('.go') ||
			item.name.toLowerCase().endsWith('.rs') ||
			item.name.toLowerCase().endsWith('.php') ||
			item.name.toLowerCase().endsWith('.rb'));

	$: isAudio =
		getContentType().startsWith('audio/') ||
		(item?.name && item?.name.toLowerCase().endsWith('.mp3')) ||
		(item?.name && item?.name.toLowerCase().endsWith('.wav')) ||
		(item?.name && item?.name.toLowerCase().endsWith('.ogg')) ||
		(item?.name && item?.name.toLowerCase().endsWith('.m4a')) ||
		(item?.name && item?.name.toLowerCase().endsWith('.webm'));

	$: isImage =
		getContentType().startsWith('image/') ||
		(item?.name &&
			(item.name.toLowerCase().endsWith('.png') ||
				item.name.toLowerCase().endsWith('.jpg') ||
				item.name.toLowerCase().endsWith('.jpeg') ||
				item.name.toLowerCase().endsWith('.gif') ||
				item.name.toLowerCase().endsWith('.webp') ||
				item.name.toLowerCase().endsWith('.svg') ||
				item.name.toLowerCase().endsWith('.bmp') ||
				item.name.toLowerCase().endsWith('.ico')));

	$: isExcel =
		getContentType() === 'application/vnd.ms-excel' ||
		getContentType() ===
			'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' ||
		getContentType() === 'text/csv' ||
		getContentType() === 'application/csv' ||
		(item?.name &&
			(item.name.toLowerCase().endsWith('.xls') ||
				item.name.toLowerCase().endsWith('.xlsx') ||
				item.name.toLowerCase().endsWith('.csv')));

	$: isDocx =
		getContentType() ===
			'application/vnd.openxmlformats-officedocument.wordprocessingml.document' ||
		(item?.name && item.name.toLowerCase().endsWith('.docx'));

	$: isPptx =
		getContentType() ===
			'application/vnd.openxmlformats-officedocument.presentationml.presentation' ||
		(item?.name && item.name.toLowerCase().endsWith('.pptx'));

	$: isText =
		getContentType().startsWith('text/') ||
		TEXT_EXTS.has(getFileExt(item?.name)) ||
		isMarkdown ||
		isCode;

	const loadExcelContent = async () => {
		try {
			excelError = '';
			const [arrayBuffer, { read }] = await Promise.all([
				getFileContentById(item.id),
				import('xlsx')
			]);
			excelWorkbook = read(arrayBuffer, { type: 'array' });
			excelSheetNames = excelWorkbook.SheetNames;

			if (excelSheetNames.length > 0) {
				selectedSheet = excelSheetNames[0];
				await renderExcelSheet();
			}
		} catch (error) {
			console.error('Error loading Excel/CSV file:', error);
			excelError = $i18n.t('Failed to load Excel/CSV file. Please try downloading it instead.');
		}
	};

	const renderExcelSheet = async () => {
		if (!excelWorkbook || !selectedSheet) return;
		const { excelToTable } = await import('$lib/utils/excelToTable');
		const worksheet = excelWorkbook.Sheets[selectedSheet];
		const result = await excelToTable(worksheet);
		excelHtml = result.html;
		rowCount = result.rowCount;
	};

	$: if (selectedSheet && excelWorkbook) {
		renderExcelSheet();
	}

	const loadDocxContent = async () => {
		try {
			docxError = '';
			const [arrayBuffer, mammoth] = await Promise.all([
				getFileContentById(item.id),
				import('mammoth')
			]);
			const result = await mammoth.convertToHtml({ arrayBuffer });
			docxHtml = DOMPurify.sanitize(result.value);
		} catch (error) {
			console.error('Error loading DOCX file:', error);
			docxError = $i18n.t('Failed to load DOCX file. Please try downloading it instead.');
		}
	};

	const loadPptxContent = async () => {
		try {
			pptxError = '';
			const [arrayBuffer, { pptxToImages }] = await Promise.all([
				getFileContentById(item.id),
				import('$lib/utils/pptxToHtml')
			]);
			const result = await pptxToImages(arrayBuffer);
			pptxSlides = result.images;
			pptxCurrentSlide = 0;
		} catch (error) {
			console.error('Error loading PPTX file:', error);
			pptxError = $i18n.t('Failed to load PPTX file. Please try downloading it instead.');
		}
	};

	const loadTextContent = async () => {
		const fileId = getFileId();
		if (!fileId) return;

		try {
			textContentError = '';
			const arrayBuffer = await getFileContentById(fileId);
			if (!arrayBuffer) return;

			textContent = new TextDecoder('utf-8').decode(arrayBuffer);
		} catch (error) {
			console.error('Error loading text file:', error);
			textContentError = $i18n.t('Failed to load file content.');
		}
	};

	const loadContent = async () => {
		selectedTab = '';
		expandedContent = false;
		textContent = '';
		textContentError = '';
		excelError = '';
		docxError = '';
		pptxError = '';
		if (item?.type === 'collection') {
			loading = true;

			const knowledge = await getKnowledgeById(localStorage.token, item.id).catch((e) => {
				console.error('Error fetching knowledge base:', e);
				return null;
			});

			if (knowledge) {
				item.files = knowledge.files || [];
			}
			loading = false;
		} else if (item?.type === 'file') {
			loading = true;

			const fileId = getFileId();

			const file = fileId
				? await getFileById(localStorage.token, fileId).catch((e) => {
						console.error('Error fetching file:', e);
						return null;
					})
				: null;

			if (file) {
				item = {
					...item,
					id: file.id ?? fileId ?? item?.id,
					meta: item?.meta ?? file.meta ?? {},
					content_type: item?.content_type ?? file?.meta?.content_type,
					file: file || {}
				};
			}

			if (isText && !getResolvedTextContent()) {
				await loadTextContent();
			}

			// Load Excel content if it's an Excel file
			if (isExcel) {
				await loadExcelContent();
			}
			if (isDocx) {
				await loadDocxContent();
			}
			if (isPptx) {
				await loadPptxContent();
			}

			loading = false;
		}

		await tick();
	};

	$: if (show) {
		loadContent();
	}

	onMount(() => {
		console.log(item);
		if (item?.context === 'full') {
			enableFullContent = true;
		}

		return () => {
			pzInstance?.dispose();
		};
	});
</script>

<Modal bind:show size="lg">
	<div class="font-primary px-4.5 py-3.5 w-full flex flex-col justify-center dark:text-gray-400">
			<div class=" pb-2">
				<div class="flex items-start justify-between">
					<div>
						<div class=" font-medium text-lg dark:text-gray-100">
							<div class="line-clamp-1">{item?.name ?? 'File'}</div>
						</div>
					</div>

					<div class="flex items-center gap-1">
						{#if item?.type === 'file' && getFileDownloadUrl()}
							<Tooltip content={$i18n.t('Download')}>
								<button
									class="p-1 rounded-md hover:bg-black/5 dark:hover:bg-white/5 transition"
									aria-label={$i18n.t('Download')}
									on:click={downloadFile}
								>
									<Download className="size-4" />
								</button>
							</Tooltip>
						{/if}

						<button
							aria-label={$i18n.t('Close')}
							on:click={() => {
								show = false;
							}}
						>
							<XMark />
						</button>
					</div>
				</div>

			<div>
				<div class="flex flex-col items-center md:flex-row gap-1 justify-between w-full">
						<div class=" flex flex-wrap text-xs gap-1 text-gray-500">
							{#if item?.type === 'collection'}
							{#if item?.type}
								<div class="capitalize shrink-0">{item.type}</div>
								•
							{/if}

							{#if item?.description}
								<div class="line-clamp-1">{item.description}</div>
								•
							{/if}

							{#if item?.created_at}
								<div class="capitalize shrink-0">
									{dayjs(item.created_at * 1000).format('LL')}
								</div>
							{/if}
						{/if}

						{#if item.size}
							<div class="capitalize shrink-0">{formatFileSize(item.size)}</div>
							•
						{/if}

							{#if getResolvedTextContent()}
								<div class="capitalize shrink-0">
									{#if isExcel && rowCount > 0 && selectedTab === 'preview'}
										{$i18n.t('{{COUNT}} Rows', {
										COUNT: rowCount
									})}
									{:else}
										{$i18n.t('{{COUNT}} extracted lines', {
											COUNT: getLineCount(getResolvedTextContent())
										})}
									{/if}
								</div>

							<div class="flex items-center gap-1 shrink-0">
								• {$i18n.t('Formatting may be inconsistent from source.')}
							</div>
						{/if}

						{#if item?.knowledge}
							<div class="capitalize shrink-0">
								{$i18n.t('Knowledge Base')}
							</div>
						{/if}
					</div>

					{#if edit}
						<div class=" self-end">
							<Tooltip
								content={enableFullContent
									? $i18n.t(
											'Inject the entire content as context for comprehensive processing, this is recommended for complex queries.'
										)
									: $i18n.t(
											'Default to segmented retrieval for focused and relevant content extraction, this is recommended for most cases.'
										)}
							>
								<div class="flex items-center gap-1.5 text-xs">
									{#if enableFullContent}
										{$i18n.t('Using Entire Document')}
									{:else}
										{$i18n.t('Using Focused Retrieval')}
									{/if}
									<Switch
										bind:state={enableFullContent}
										on:change={(e) => {
											item.context = e.detail ? 'full' : undefined;
										}}
									/>
								</div>
							</Tooltip>
						</div>
					{/if}
				</div>
			</div>
		</div>

		<div class="max-h-[75vh] overflow-auto">
			{#if !loading}
				{#if item?.type === 'collection'}
					<div>
						{#each item?.files as file}
							<div class="flex items-center gap-2 mb-2">
								<div class="flex-shrink-0 text-xs">
									{file?.meta?.name}
								</div>
							</div>
						{/each}
					</div>
				{/if}

					{#if isAudio || isPDF || isExcel || isCode || isMarkdown || isDocx || isPptx}
					<div
						class="flex mb-2.5 scrollbar-none overflow-x-auto w-full border-b border-gray-50 dark:border-gray-850/30 text-center text-sm font-medium bg-transparent dark:text-gray-200"
					>
						<button
							class="min-w-fit py-1.5 px-4 border-b {selectedTab === ''
								? ' '
								: ' border-transparent text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
							type="button"
							on:click={() => {
								selectedTab = '';
							}}>{$i18n.t('Content')}</button
						>

						<button
							class="min-w-fit py-1.5 px-4 border-b {selectedTab === 'preview'
								? ' '
								: ' border-transparent text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
							type="button"
							on:click={() => {
								selectedTab = 'preview';
							}}>{$i18n.t('Preview')}</button
						>
					</div>
				{/if}

					{#if isImage}
						<div class="relative w-full max-h-[70vh] overflow-hidden">
						<div class="absolute top-2 right-2 z-10">
							<Tooltip content={$i18n.t('Reset view')}>
								<button
									class="p-1.5 rounded-lg bg-white/80 dark:bg-gray-850/80 backdrop-blur-sm shadow-sm hover:bg-gray-100 dark:hover:bg-gray-800 transition text-gray-500 dark:text-gray-400"
									on:click={resetImageView}
								>
									<Reset className="size-4" />
								</button>
							</Tooltip>
						</div>
							<div use:initImagePanzoom>
								<img
									src={getFileContentUrl()}
									alt={item?.name ?? 'Image'}
									class="w-full object-contain rounded-lg"
									loading="lazy"
								draggable="false"
							/>
						</div>
					</div>
					{:else if selectedTab === ''}
						{#if getResolvedTextContent() || textContentError}
							{@const rawContent = getResolvedTextContent().trim() || 'No content'}
							{@const isTruncated =
								($settings?.renderMarkdownInPreviews ?? true) &&
								rawContent.length > CONTENT_PREVIEW_LIMIT &&
								!expandedContent}
							{#if textContentError}
								<div class="text-red-500 text-sm p-4">{textContentError}</div>
							{:else if $settings?.renderMarkdownInPreviews ?? true}
								<div
									class="max-h-96 overflow-scroll scrollbar-hidden text-sm prose dark:prose-invert max-w-full"
							>
								<Markdown
									content={isTruncated ? rawContent.slice(0, CONTENT_PREVIEW_LIMIT) : rawContent}
									id="file-preview"
								/>
							</div>
							{#if isTruncated}
								<button
									class="mt-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
									on:click={() => {
										expandedContent = true;
									}}
								>
									{$i18n.t('Show all ({{COUNT}} characters)', {
										COUNT: rawContent.length.toLocaleString()
									})}
								</button>
							{/if}
						{:else}
							<div class="max-h-96 overflow-scroll scrollbar-hidden text-xs whitespace-pre-wrap">
								{rawContent}
							</div>
						{/if}
						{/if}
					{:else if selectedTab === 'preview'}
						{#if isAudio}
							<audio
								src={getFileContentUrl()}
								class="w-full border-0 rounded-lg mb-2"
								controls
								playsinline
							/>
						{:else if isPDF}
							<PDFViewer
								url={getFileContentUrl()}
								className="w-full h-[70vh] border-0 rounded-lg"
							/>
					{:else if isExcel}
						{#if excelError}
							<div class="text-red-500 text-sm p-4">
								{excelError}
							</div>
						{:else}
							{#if excelSheetNames.length > 1}
								<div
									class="flex mb-2.5 scrollbar-none overflow-x-auto w-full border-b border-gray-50 dark:border-gray-850/30 text-center text-sm font-medium bg-transparent dark:text-gray-200"
								>
									{#each excelSheetNames as sheetName}
										<button
											class="min-w-fit py-1.5 px-4 border-b {selectedSheet === sheetName
												? ' '
												: ' border-transparent text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
											type="button"
											on:click={() => {
												selectedSheet = sheetName;
											}}>{sheetName}</button
										>
									{/each}
								</div>
							{/if}

							{#if excelHtml}
								<div class="office-preview overflow-auto max-h-[60vh]">
									{@html excelHtml}
								</div>
							{:else}
								<div class="text-gray-500 text-sm p-4">No content available</div>
							{/if}
						{/if}
						{:else if isCode}
							<div class="max-h-[60vh] overflow-scroll scrollbar-hidden text-sm relative">
								<CodeBlock
									code={getResolvedTextContent()}
									lang={item.name.split('.').pop()}
									token={null}
									edit={false}
								run={false}
								save={false}
							/>
						</div>
						{:else if isMarkdown}
							<div
								class="max-h-[60vh] overflow-scroll scrollbar-hidden text-sm prose dark:prose-invert max-w-full"
							>
								<Markdown content={getResolvedTextContent()} id="markdown-viewer" />
							</div>
					{:else if isDocx}
						{#if docxError}
							<div class="text-red-500 text-sm p-4">{docxError}</div>
						{:else if docxHtml}
							<div
								class="office-preview max-h-[60vh] overflow-auto p-4 prose dark:prose-invert max-w-full text-sm"
							>
								{@html docxHtml}
							</div>
						{:else}
							<div class="text-gray-500 text-sm p-4">No content available</div>
						{/if}
					{:else if isPptx}
						{#if pptxError}
							<div class="text-red-500 text-sm p-4">{pptxError}</div>
						{:else if pptxSlides.length > 0}
							<div class="max-h-[60vh] overflow-auto">
								<div class="flex justify-center p-4">
									<img
										src={pptxSlides[pptxCurrentSlide]}
										alt="Slide {pptxCurrentSlide + 1}"
										class="max-w-full max-h-[50vh] object-contain rounded-md shadow-lg"
										draggable="false"
									/>
								</div>
								{#if pptxSlides.length > 1}
									<div class="flex items-center justify-center gap-3 pb-3 text-sm text-gray-500">
										<button
											class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
											disabled={pptxCurrentSlide === 0}
											on:click={() => (pptxCurrentSlide = Math.max(0, pptxCurrentSlide - 1))}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												viewBox="0 0 20 20"
												fill="currentColor"
												class="size-5"
											>
												<path
													fill-rule="evenodd"
													d="M11.78 5.22a.75.75 0 0 1 0 1.06L8.06 10l3.72 3.72a.75.75 0 1 1-1.06 1.06l-4.25-4.25a.75.75 0 0 1 0-1.06l4.25-4.25a.75.75 0 0 1 1.06 0Z"
													clip-rule="evenodd"
												/>
											</svg>
										</button>
										<span>{pptxCurrentSlide + 1} / {pptxSlides.length}</span>
										<button
											class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
											disabled={pptxCurrentSlide === pptxSlides.length - 1}
											on:click={() =>
												(pptxCurrentSlide = Math.min(pptxSlides.length - 1, pptxCurrentSlide + 1))}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												viewBox="0 0 20 20"
												fill="currentColor"
												class="size-5"
											>
												<path
													fill-rule="evenodd"
													d="M8.22 5.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L11.94 10 8.22 6.28a.75.75 0 0 1 0-1.06Z"
													clip-rule="evenodd"
												/>
											</svg>
										</button>
									</div>
								{/if}
							</div>
						{:else}
							<div class="text-gray-500 text-sm p-4">No content available</div>
						{/if}
					{:else}
						<div class="max-h-96 overflow-scroll scrollbar-hidden text-xs whitespace-pre-wrap">
							{(item?.file?.data?.content ?? '').trim() || 'No content'}
						</div>
					{/if}
				{/if}
			{:else}
				<div class="flex items-center justify-center py-6">
					<Spinner className="size-5" />
				</div>
			{/if}
		</div>
	</div>
</Modal>

<style>
	:global(.excel-table-container table) {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.875rem;
		line-height: 1.25rem;
	}

	:global(.excel-table-container table td),
	:global(.excel-table-container table th) {
		border-width: 1px;
		border-style: solid;
		border-color: var(--color-gray-300, #cdcdcd);
		padding: 0.5rem 0.75rem;
		text-align: left;
	}

	:global(.dark .excel-table-container table td),
	:global(.dark .excel-table-container table th) {
		border-color: var(--color-gray-600, #676767);
	}

	:global(.excel-table-container table th) {
		background-color: var(--color-gray-100, #ececec);
		font-weight: 600;
	}

	:global(.dark .excel-table-container table th) {
		background-color: var(--color-gray-800, #333);
		color: var(--color-gray-100, #ececec);
	}

	:global(.excel-table-container table tr:nth-child(even)) {
		background-color: var(--color-gray-50, #f9f9f9);
	}

	:global(.dark .excel-table-container table tr:nth-child(even)) {
		background-color: rgba(38, 38, 38, 0.5);
	}

	:global(.excel-table-container table tr:hover) {
		background-color: var(--color-gray-100, #ececec);
	}

	:global(.dark .excel-table-container table tr:hover) {
		background-color: rgba(51, 51, 51, 0.5);
	}
</style>
