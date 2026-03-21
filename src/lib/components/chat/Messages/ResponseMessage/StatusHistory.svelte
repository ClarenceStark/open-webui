<script>
	import { getContext } from 'svelte';

	import StatusItem from './StatusHistory/StatusItem.svelte';

	const i18n = getContext('i18n');

	export let statusHistory = [];
	export let toolCallDetails = [];

	const HIDDEN_STATUS_ACTIONS = new Set(['artifact_uploaded', 'download_artifact', 'view_image']);

	const isVisibleStatus = (item) =>
		item && item.hidden !== true && !HIDDEN_STATUS_ACTIONS.has(item.action);

	const parseJSONDeep = (value) => {
		if (typeof value !== 'string' || value.trim() === '') {
			return value;
		}

		try {
			return parseJSONDeep(JSON.parse(value));
		} catch {
			return value;
		}
	};

	const trimTrailingEllipsis = (value) => (value ?? '').replace(/(?:\.\.\.|…)\s*$/, '').trim();

	const normalizeToolName = (value) => {
		switch (value) {
			case 'web_search':
			case 'web_search_preview':
				return 'web_search';
			case 'open_page':
				return 'open_page';
			case 'find_in_page':
				return 'find_in_page';
			default:
				return value ?? '';
		}
	};

	const normalizeStatusAction = (item) => {
		if (!item) {
			return '';
		}

		if (
			['web_search', 'web_search_preview', 'web_search_queries_generated', 'sources_retrieved'].includes(
				item.action
			) ||
			item.description === 'Searching the web'
		) {
			return 'web_search';
		}

		if (item.action === 'open_page') {
			return 'open_page';
		}

		if (item.action === 'find_in_page') {
			return 'find_in_page';
		}

		return item.action ?? '';
	};

	const getStatusTitle = (item) => {
		if (!item) {
			return '';
		}

		if (item?.description?.includes('{{count}}')) {
			return $i18n.t(item.description, {
				count: (item?.urls || item?.items || []).length
			});
		}

		if (item?.description?.includes('{{searchQuery}}')) {
			return $i18n.t(item.description, {
				searchQuery: item?.query
			});
		}

		if (item?.description === 'No search query generated') {
			return $i18n.t('No search query generated');
		}

		if (item?.description === 'Generating search query') {
			return $i18n.t('Generating search query');
		}

		if (item?.action === 'knowledge_search') {
			return $i18n.t('Searching Knowledge for "{{searchQuery}}"', {
				searchQuery: item?.query
			});
		}

		if (item?.action === 'web_search_queries_generated' && item?.queries?.length) {
			return $i18n.t('Searching the web');
		}

		if (item?.action === 'queries_generated' && item?.queries?.length) {
			return $i18n.t('Querying');
		}

		if (item?.description === 'Searching the web') {
			return trimTrailingEllipsis($i18n.t('Searching the web'));
		}

		if (item?.action === 'open_page') {
			return trimTrailingEllipsis($i18n.t('Opening page'));
		}

		if (item?.action === 'find_in_page') {
			return trimTrailingEllipsis($i18n.t('Finding in page'));
		}

		return trimTrailingEllipsis(item?.description ?? item?.action ?? '');
	};

	const getStatusSubtitle = (item) => {
		if (!item) {
			return '';
		}

		if (item?.command) {
			return item.command;
		}

		if (item?.pattern || item?.query || item?.url) {
			return item.pattern || item.query || item.url;
		}

		if (item?.path || item?.workdir) {
			return item.path || item.workdir;
		}

		if (item?.files?.[0]?.name) {
			return item.files[0].name;
		}

		if (item?.queries?.length) {
			return item.queries.join(' · ');
		}

		return '';
	};

	const getToolTitle = (toolCall) => {
		switch (normalizeToolName(toolCall?.attributes?.name)) {
			case 'web_search':
				return trimTrailingEllipsis($i18n.t('Searching the web'));
			case 'open_page':
				return trimTrailingEllipsis($i18n.t('Opening page'));
			case 'find_in_page':
				return trimTrailingEllipsis($i18n.t('Finding in page'));
			default:
				return trimTrailingEllipsis(toolCall?.attributes?.name ?? '');
		}
	};

	const getToolSubtitle = (toolCall) => {
		const args = parseJSONDeep(toolCall?.attributes?.arguments ?? '');
		if (!args || typeof args !== 'object' || Array.isArray(args)) {
			return '';
		}

		return (
			args.query ??
			args.searchQuery ??
			args.q ??
			args.pattern ??
			args.url ??
			args.ref_id ??
			args.path ??
			args.workdir ??
			''
		);
	};

	const isToolLikeStatus = (status) =>
		['web_search', 'open_page', 'find_in_page', 'knowledge_search'].includes(
			normalizeStatusAction(status)
		);

	let timelineEntries = [];

	$: compressedStatusHistory = (statusHistory ?? [])
		.filter(isVisibleStatus)
		.reduce((acc, status) => {
			const previous = acc.at(-1);
			if (!previous) {
				acc.push(status);
				return acc;
			}

			const previousAction = normalizeStatusAction(previous);
			const currentAction = normalizeStatusAction(status);
			const previousSubtitle = getStatusSubtitle(previous);
			const currentSubtitle = getStatusSubtitle(status);

			if (
				previousAction !== '' &&
				previousAction === currentAction &&
				previousSubtitle === '' &&
				currentSubtitle !== ''
			) {
				acc[acc.length - 1] = status;
				return acc;
			}

			acc.push(status);
			return acc;
		}, []);

	$: {
		const usedStatusIndexes = new Set();
		const entries = [];

		for (const [toolIdx, toolCall] of (toolCallDetails ?? []).entries()) {
			const normalizedTool = normalizeToolName(toolCall?.attributes?.name);
			let matchedStatusIndex = -1;

			for (let idx = 0; idx < compressedStatusHistory.length; idx += 1) {
				if (usedStatusIndexes.has(idx)) {
					continue;
				}

				const status = compressedStatusHistory[idx];
				if (normalizeStatusAction(status) !== normalizedTool) {
					continue;
				}

				matchedStatusIndex = idx;
				break;
			}

			if (matchedStatusIndex !== -1) {
				usedStatusIndexes.add(matchedStatusIndex);
			}

			const matchedStatus =
				matchedStatusIndex !== -1 ? compressedStatusHistory[matchedStatusIndex] : null;

			entries.push({
				id: `tool-${toolIdx}`,
				status: matchedStatus,
				title: matchedStatus ? getStatusTitle(matchedStatus) : getToolTitle(toolCall),
				subtitle: matchedStatus ? getStatusSubtitle(matchedStatus) : getToolSubtitle(toolCall)
			});
		}

		for (const [statusIdx, status] of compressedStatusHistory.entries()) {
			if (usedStatusIndexes.has(statusIdx) || !isToolLikeStatus(status)) {
				continue;
			}

			entries.push({
				id: `status-${statusIdx}`,
				status,
				title: getStatusTitle(status),
				subtitle: getStatusSubtitle(status)
			});
		}

		timelineEntries = entries.filter((entry) => entry.title);
	}
</script>

{#if timelineEntries.length > 0}
	<div class="text-sm flex flex-col w-full">
		<div class="space-y-1">
			{#each timelineEntries as entry, idx}
				<div class="flex items-stretch gap-2 mb-1">
					<div>
						<div class="pt-3 px-1 mb-1.5">
							<span class="relative flex size-1.5 rounded-full justify-center items-center">
								<span
									class="relative inline-flex size-1.5 rounded-full bg-gray-500 dark:bg-gray-400"
								></span>
							</span>
						</div>
						{#if idx !== timelineEntries.length - 1}
							<div
								class="w-[0.5px] ml-[6.5px] h-[calc(100%-14px)] bg-gray-300 dark:bg-gray-700"
							></div>
						{/if}
					</div>

					<div class="flex-1 min-w-0 pt-1">
						<StatusItem
							status={entry.status}
							done={true}
							titleOverride={entry.title}
							subtitleOverride={entry.subtitle}
						/>
					</div>
				</div>
			{/each}
		</div>
	</div>
{/if}
