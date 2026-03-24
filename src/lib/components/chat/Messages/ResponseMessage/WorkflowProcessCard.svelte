<script lang="ts">
	import { decode } from 'html-entities';
	import { getContext } from 'svelte';

	import ContentRenderer from '../ContentRenderer.svelte';
	import StatusHistory from './StatusHistory.svelte';
	import CodeExecutions from '../CodeExecutions.svelte';
	import Image from '$lib/components/common/Image.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import { removeAllDetails } from '$lib/utils';

	const i18n = getContext('i18n');

	export let chatId = '';
	export let history;
	export let selectedModels = [];
	export let messages = [];
	export let model = null;
	export let editCodeBlock = true;
	export let expanded = false;

	const TOOL_CALL_DETAILS_REGEX =
		/<details\b(?=[^>]*\btype="tool_calls")[^>]*>[\s\S]*?<\/details>/gi;

	const parseDetailAttributes = (detail: string) => {
		const attributes = {};
		const attributesRegex = /(\w+)="([^"]*)"/g;
		let match;

		while ((match = attributesRegex.exec(detail)) !== null) {
			attributes[match[1]] = decode(match[2] ?? '');
		}

		return attributes;
	};

	const extractToolCallDetails = (content: string) => {
		if (typeof content !== 'string' || !content.includes('type="tool_calls"')) {
			return [];
		}

		return Array.from(content.matchAll(TOOL_CALL_DETAILS_REGEX)).map((match, idx) => ({
			id: `workflow-tool-call-${idx}`,
			attributes: parseDetailAttributes(match[0] ?? '')
		}));
	};

	const getDisplayedFiles = (files) => {
		if (!Array.isArray(files) || files.length === 0) {
			return [];
		}

		const finalOutputFiles = files.filter((file) => file?.is_final_output === true);
		return finalOutputFiles.length > 0 ? finalOutputFiles : files;
	};

	const getVisibleTextSummary = (content: string) =>
		removeAllDetails(content ?? '')
			.replace(/\s+/g, ' ')
			.trim();

	const getWorkProcessLabel = () => {
		const lang = ($i18n?.language ?? '').toLowerCase();
		return lang.startsWith('en') ? 'Work Process' : '工作过程';
	};
</script>

<div class="overflow-hidden">
	<button
		type="button"
		class="inline-flex items-center gap-1.5 py-1 text-left transition"
		aria-expanded={expanded}
		on:click={() => {
			expanded = !expanded;
		}}
	>
		<div class="text-[1rem] font-normal tracking-[0.06em] text-[#9c9c9c] dark:text-[#9c9c9c]">
			{getWorkProcessLabel()}
		</div>
		<div
			class="shrink-0 translate-y-[1px] text-[#9c9c9c] transition-transform duration-200 ease-out dark:text-[#9c9c9c]"
			class:rotate-180={expanded}
		>
			<ChevronDown className="size-3.5" strokeWidth="2.8" />
		</div>
	</button>

	<div
		class={`grid transition-[grid-template-rows,opacity] duration-200 ease-out ${
			expanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
		}`}
	>
		<div class="overflow-hidden">
			<div class="space-y-4 pt-2">
				{#each messages as workflowMessage (workflowMessage.id)}
					<div class="space-y-3">
						{#if workflowMessage.content && workflowMessage.error !== true}
							<ContentRenderer
								id={`${chatId}-${workflowMessage.id}-workflow`}
								messageId={workflowMessage.id}
								{history}
								{selectedModels}
								content={workflowMessage.content}
								sources={workflowMessage.sources}
								floatingButtons={false}
								save={false}
								preview={false}
								{editCodeBlock}
								topPadding={false}
								done={true}
								isComplete={true}
								{model}
							/>
						{/if}

						{#if workflowMessage.code_executions}
							<div>
								<CodeExecutions codeExecutions={workflowMessage.code_executions} />
							</div>
						{/if}

						{#if getDisplayedFiles(workflowMessage.files ?? []).length > 0}
							<div class="flex w-full flex-wrap gap-2 overflow-x-auto">
								{#each getDisplayedFiles(workflowMessage.files ?? []) as file}
									<div>
										{#if file.type === 'image' || (file?.content_type ?? '').startsWith('image/')}
											<Image
												src={file.url}
												alt={getVisibleTextSummary(workflowMessage.content ?? '')}
											/>
										{:else}
											<FileItem
												item={file}
												url={file.url}
												name={file.name}
												type={file.type}
												size={file?.size}
												small={true}
											/>
										{/if}
									</div>
								{/each}
							</div>
						{/if}

						{#if (workflowMessage.statusHistory ?? []).length > 0 || extractToolCallDetails(workflowMessage.content ?? '').length > 0}
							<div>
								<StatusHistory
									statusHistory={workflowMessage.statusHistory}
									toolCallDetails={extractToolCallDetails(workflowMessage.content ?? '')}
								/>
							</div>
						{/if}
					</div>
				{/each}

				<div class="mt-2 border-t border-[#9c9c9c] pt-2 dark:border-[#9c9c9c]"></div>
			</div>
		</div>
	</div>
</div>
