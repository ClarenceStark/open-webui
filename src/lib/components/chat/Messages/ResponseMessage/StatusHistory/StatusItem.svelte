<script>
	import { getContext } from 'svelte';
	import {
		getLocalizedToolActionLabel,
		getLocalizedToolDescriptionLabel,
		trimTrailingEllipsis
	} from '../../toolStatus';
	const i18n = getContext('i18n');

	export let status = null;
	export let done = false;
	export let titleOverride = '';
	export let subtitleOverride = '';

	const resolveDone = (item, forceDone = false) => (forceDone || item?.done) === true;
	const getTitle = (item) => {
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
			return $i18n.t(`Searching Knowledge for "{{searchQuery}}"`, {
				searchQuery: item?.query
			});
		}

		if (item?.action === 'web_search_queries_generated' && item?.queries?.length) {
			return $i18n.t('Searching');
		}

		if (item?.action === 'queries_generated' && item?.queries?.length) {
			return $i18n.t('Querying');
		}

		if (item?.action === 'sources_retrieved' && item?.count !== undefined) {
			if (item.count === 0) {
				return $i18n.t('No sources found');
			}

			if (item.count === 1) {
				return $i18n.t('Retrieved 1 source');
			}

			return $i18n.t('Retrieved {{count}} sources', {
				count: item.count
			});
		}

		if (item?.description === 'Searching the web') {
			return trimTrailingEllipsis($i18n.t('Searching the web'));
		}

		const localizedToolDescription = getLocalizedToolDescriptionLabel(item?.description, $i18n.t);
		if (localizedToolDescription) {
			return localizedToolDescription;
		}

		const localizedToolAction = getLocalizedToolActionLabel(item?.action, $i18n.t);
		if (localizedToolAction) {
			return localizedToolAction;
		}

		if (item?.action === 'artifact_uploaded') {
			return (item?.files ?? []).length > 1
				? $i18n.t('Generated {{count}} files', { count: item.files.length })
				: $i18n.t('Generated file');
		}

		if (item?.description) {
			return trimTrailingEllipsis(item.description);
		}

		return trimTrailingEllipsis(item?.action ?? '');
	};

	const getSubtitle = (item) => {
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

	$: isDone = resolveDone(status, done);
	$: title = titleOverride || getTitle(status);
	$: subtitle = subtitleOverride || getSubtitle(status);
</script>

{#if !status?.hidden}
	<div class="status-description flex items-start gap-2 py-0.5 w-full text-left">
		<div class="min-w-0 flex-1 flex flex-col justify-center -space-y-0.5">
			<div class="text-base line-clamp-1 text-wrap text-gray-500 dark:text-gray-400">
				{title}
			</div>

			{#if subtitle}
				<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1">
					{subtitle}
				</div>
			{/if}
		</div>
	</div>
{/if}
