<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');
	import WebSearchResults from '../WebSearchResults.svelte';
	import Search from '$lib/components/icons/Search.svelte';

	export let status = null;
	export let done = false;
</script>

{#if !status?.hidden}
	<div class="status-description flex items-center gap-2 py-0.5 w-full text-left">
		{#if ['web_search', 'web_search_preview'].includes(status?.action) && (status?.urls || status?.items || status?.queries)}
			<WebSearchResults {status}>
				<div class="flex flex-col justify-center -space-y-0.5">
					<div
						class="{(done || status?.done) === false
							? 'shimmer'
							: ''} text-base line-clamp-1 text-wrap"
					>
						{#if status?.description?.includes('{{count}}')}
							{$i18n.t(status?.description, {
								count: (status?.urls || status?.items).length
							})}
						{:else if status?.description === 'No search query generated'}
							{$i18n.t('No search query generated')}
						{:else if status?.description === 'Generating search query'}
							{$i18n.t('Generating search query')}
						{:else}
							{status?.description || $i18n.t('Searching the web')}
						{/if}
					</div>

					{#if status?.queries?.length > 0}
						<div class="flex gap-1 flex-wrap mt-2">
							{#each status.queries as query (query)}
								<div
									class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs"
								>
									<div>
										<Search className="size-3" />
									</div>
									<span class="line-clamp-1">{query}</span>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			</WebSearchResults>
		{:else if status?.action === 'open_page'}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{status?.description || $i18n.t('Opening page')}
				</div>
				{#if status?.url}
					<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1">{status.url}</div>
				{/if}
			</div>
		{:else if status?.action === 'find_in_page'}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{status?.description || $i18n.t('Finding in page')}
				</div>
				<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1">
					{status?.pattern || status?.query || status?.url}
				</div>
			</div>
		{:else if status?.action === 'artifact_uploaded'}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div class="text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap">
					{#if (status?.files ?? []).length > 1}
						{$i18n.t('Generated {{count}} files', { count: status.files.length })}
					{:else}
						{status?.description || $i18n.t('Generated file')}
					{/if}
				</div>
				{#if status?.files?.[0]?.name}
					<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1">
						{status.files[0].name}
					</div>
				{/if}
			</div>
		{:else if status?.action === 'knowledge_search'}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{$i18n.t(`Searching Knowledge for "{{searchQuery}}"`, {
						searchQuery: status.query
					})}
				</div>
			</div>
		{:else if status?.action === 'web_search_queries_generated' && status?.queries}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{$i18n.t(`Searching`)}
				</div>

				<div class=" flex gap-1 flex-wrap mt-2">
					{#each status.queries as query, idx (query)}
						<div
							class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs"
						>
							<div>
								<Search className="size-3" />
							</div>

							<span class="line-clamp-1">
								{query}
							</span>
						</div>
					{/each}
				</div>
			</div>
		{:else if status?.action === 'queries_generated' && status?.queries}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{$i18n.t(`Querying`)}
				</div>

				<div class=" flex gap-1 flex-wrap mt-2">
					{#each status.queries as query, idx (query)}
						<div
							class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs"
						>
							<div>
								<Search className="size-3" />
							</div>

							<span class="line-clamp-1">
								{query}
							</span>
						</div>
					{/each}
				</div>
			</div>
		{:else if status?.action === 'sources_retrieved' && status?.count !== undefined}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{#if status.count === 0}
						{$i18n.t('No sources found')}
					{:else if status.count === 1}
						{$i18n.t('Retrieved 1 source')}
					{:else}
						<!-- {$i18n.t('Source')} -->
						<!-- {$i18n.t('No source available')} -->
						<!-- {$i18n.t('No distance available')} -->
						<!-- {$i18n.t('Retrieved {{count}} sources')} -->
						{$i18n.t('Retrieved {{count}} sources', {
							count: status.count
						})}
					{/if}
				</div>
			</div>
		{:else if ['exec_command', 'run_command', 'write_stdin', 'list_files', 'read_file', 'download_artifact', 'view_image', 'apply_patch', 'write_file', 'replace_file_content', 'display_file'].includes(status?.action)}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					{status?.description}
				</div>
				{#if status?.command}
					<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1">
						{status.command}
					</div>
				{:else if status?.path || status?.workdir}
					<div class="text-xs text-gray-400 dark:text-gray-500 line-clamp-1">
						{status.path || status.workdir}
					</div>
				{/if}
			</div>
		{:else}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
				>
					<!-- $i18n.t(`Searching "{{searchQuery}}"`) -->
					{#if status?.description?.includes('{{searchQuery}}')}
						{$i18n.t(status?.description, {
							searchQuery: status?.query
						})}
					{:else if status?.description === 'No search query generated'}
						{$i18n.t('No search query generated')}
					{:else if status?.description === 'Generating search query'}
						{$i18n.t('Generating search query')}
					{:else if status?.description === 'Searching the web'}
						{$i18n.t('Searching the web')}
					{:else}
						{status?.description}
					{/if}
				</div>
			</div>
		{/if}
	</div>
{/if}
