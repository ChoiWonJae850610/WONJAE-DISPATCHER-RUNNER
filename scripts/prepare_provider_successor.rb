#!/usr/bin/env ruby
# frozen_string_literal: true

# Generic provider-success handoff: create a state-only PR, never merge/run it.
require "base64"
require "json"
require "open3"
require "optparse"
require "yaml"

class HandoffError < StandardError; end
TYPES = %w[SOURCE_READY PROVIDER_GATE MANUAL_QA DECISION_REQUIRED NONE].freeze

def demand(ok, reason)
  raise HandoffError, reason unless ok
end

def action_ok?(action)
  return false unless action.is_a?(Hash) && TYPES.include?(action["type"])
  return false unless action["title"].is_a?(String) && !action["title"].strip.empty?
  scope = action["source_scope"]
  return false unless scope.is_a?(Array) &&
                      scope.all? { |x| x.is_a?(String) && !x.strip.empty? }
  return false if action["type"] == "SOURCE_READY" &&
                  (scope.empty? || action["source_task_id"].to_s.empty?)
  return false if action["type"] == "PROVIDER_GATE" && action["gate"].to_s.empty?
  return false if action["type"] == "MANUAL_QA" &&
                  (action.dig("manual_qa", "mode") != "owner_checkout" ||
                   !action.dig("manual_qa", "required_paths").is_a?(Array) ||
                   action.dig("manual_qa", "required_paths").empty?)
  true
end

def successor_state(state, registry, args)
  project, gate = args.values_at(:project, :gate)
  demand(state.is_a?(Hash) && state["schema_version"] == 1 &&
         state["project"] == project, "STATE_IDENTITY_MISMATCH")
  registration = registry.dig("projects", project)
  execution = registration.is_a?(Hash) && registration["execution"]
  demand(registration.is_a?(Hash) && registration["status"] == "active" &&
         registration["repository"] == args[:repository] &&
         registration["branch"] == args[:target_branch] &&
         execution.is_a?(Hash) && execution["mode"] == "direct_worker" &&
         execution["state_path"] == args[:state_path], "CONTROL_ROUTE_MISMATCH")
  gates = execution["provider_gates"]
  config = gates.is_a?(Hash) && gates[gate]
  demand(config.is_a?(Hash) && config["approval_mode"] == "sanjinworks_owner_button" &&
         config["executor"] != "approval_only" &&
         config["production"] == false && config["destructive"] == false,
         "PROVIDER_SCOPE_NOT_ELIGIBLE")
  current = state["next_action"]
  demand(action_ok?(current) && current["type"] == "PROVIDER_GATE" &&
         current["gate"] == gate, "CURRENT_GATE_CHANGED")
  next_action = state["after_provider_success"]
  return nil if next_action.nil?
  demand(action_ok?(next_action), "SUCCESSOR_ACTION_INVALID")
  if next_action["type"] == "PROVIDER_GATE"
    successor = gates[next_action["gate"]]
    demand(next_action["gate"] != gate && successor.is_a?(Hash) &&
           successor["approval_mode"] == "sanjinworks_owner_button" &&
           successor["ready_for_owner"] == true &&
           successor["executor"].is_a?(String) &&
           successor["executor"] != "approval_only" &&
           successor["production"] == false && successor["destructive"] == false,
           "SUCCESSOR_PROVIDER_NOT_READY")
  end
  after_source = state["after_source_success"]
  queue = state.fetch("source_success_queue", [])
  demand(queue.is_a?(Array) && queue.size <= 8, "SUCCESSOR_QUEUE_INVALID")
  if next_action["type"] == "SOURCE_READY"
    demand(action_ok?(after_source), "SOURCE_SUCCESSOR_REQUIRES_TERMINAL")
    demand(queue.empty? || (after_source["type"] == "SOURCE_READY" &&
           queue.all? { |x| action_ok?(x) } && queue[-1]["type"] != "SOURCE_READY"),
           "SUCCESSOR_QUEUE_INVALID")
    demand(after_source["type"] != "SOURCE_READY" || !queue.empty?,
           "SOURCE_SUCCESSOR_REQUIRES_TERMINAL")
  else
    demand(after_source.nil? && queue.empty?, "UNEXPECTED_SOURCE_QUEUE")
  end
  copy = Marshal.load(Marshal.dump(state))
  copy["next_action"] = next_action
  copy.delete("after_provider_success")
  YAML.dump(copy)
end

def command(*parts)
  stdout, _stderr, status = Open3.capture3(*parts)
  demand(status.success?, "GITHUB_OR_GIT_COMMAND_FAILED")
  stdout.strip
end

def api(path, method: "GET", fields: {})
  cmd = ["gh", "api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
  fields.each { |key, value| cmd += ["-f", "#{key}=#{value}"] }
  body = command(*cmd)
  body.empty? ? {} : JSON.parse(body)
end

def prepare_pr(args, transformed)
  demand(ENV["GH_TOKEN"].to_s.length > 10, "PRODUCT_WRITE_TOKEN_UNAVAILABLE")
  root = File.expand_path(args.fetch(:product_checkout))
  sha = args.fetch(:source_sha)
  repo = args.fetch(:repository)
  branch = args.fetch(:target_branch)
  gate = args.fetch(:gate)
  demand(command("git", "-C", root, "rev-parse", "HEAD") == sha, "LOCAL_SOURCE_MOVED")
  demand(api("repos/#{repo}/branches/#{branch}").dig("commit", "sha") == sha,
         "REMOTE_SOURCE_MOVED")
  pulls = api("repos/#{repo}/pulls?state=open&base=#{branch}&per_page=100")
  demand(pulls.is_a?(Array) && pulls.size <= 25, "PR_INVENTORY_UNVERIFIED")
  name = "docs/provider-success-#{args.fetch(:project).downcase}-#{gate.tr('_', '-')}-#{sha[0, 10]}"
  if pulls.any? { |pull| pull.dig("head", "ref") == name }
    puts "PROVIDER_SUCCESSOR_PR=ALREADY_PREPARED"
    return
  end
  demand(pulls.empty?, "OTHER_PRODUCT_PR_OPEN")
  state_path = args.fetch(:state_path)
  blob = command("git", "-C", root, "rev-parse", "#{sha}:#{state_path}")
  api("repos/#{repo}/git/refs", method: "POST",
      fields: { "ref" => "refs/heads/#{name}", "sha" => sha })
  api("repos/#{repo}/contents/#{state_path}", method: "PUT", fields: {
    "message" => "docs: advance #{args.fetch(:project)} after #{gate} provider PASS",
    "content" => Base64.strict_encode64(transformed), "sha" => blob, "branch" => name
  })
  pr = api("repos/#{repo}/pulls", method: "POST", fields: {
    "title" => "docs: #{args.fetch(:project)} next Owner stage after #{gate} PASS",
    "head" => name, "base" => branch,
    "body" => "Provider run: #{args.fetch(:run_url)}\nSource SHA: #{sha}\n\n" \
              "Predeclared state-only handoff. Separate SANJINWORKS Owner " \
              "document-merge click and exact PR-head/integrated CI required. " \
              "No product source, provider re-run, Production or next-stage execution."
  })
  puts "PROVIDER_SUCCESSOR_PR=#{pr.fetch('html_url')}"
  if ENV["GITHUB_STEP_SUMMARY"]
    File.open(ENV["GITHUB_STEP_SUMMARY"], "a") do |f|
      f.puts "\n## Next stage document PR (NOT_RUN)"
      f.puts "- Owner review: #{pr.fetch('html_url')}"
      f.puts "- Current source SHA: #{sha}"
      f.puts "- Next stage execution: NOT_RUN; requires separate Owner clicks"
    end
  end
end

args = { dry_run: false }
OptionParser.new do |parser|
  %i[project gate repository target_branch source_sha state_path
     product_checkout control_checkout run_url].each do |key|
    parser.on("--#{key.to_s.tr('_', '-')} VALUE") { |value| args[key] = value }
  end
  parser.on("--dry-run") { args[:dry_run] = true }
end.parse!

begin
  required = %i[project gate repository target_branch source_sha state_path
                product_checkout control_checkout run_url]
  demand(required.all? { |key| args[key].is_a?(String) && !args[key].strip.empty? },
         "REQUIRED_ARGUMENTS_MISSING")
  demand(args[:source_sha].match?(/\A[a-f0-9]{40}\z/) &&
         !%w[main master].include?(args[:target_branch]) &&
         args[:state_path] == ".wonjae/execution-state.yaml" &&
         args[:gate].match?(/\A[a-z][a-z0-9_]*\z/) &&
         args[:project].match?(/\A[A-Z][A-Z0-9_-]*\z/) &&
         args[:repository] == "ChoiWonJae850610/#{args[:project]}",
         "HANDOFF_IDENTITY_UNSAFE")
  state_file = File.join(args[:product_checkout], args[:state_path])
  demand(File.file?(state_file) && !File.symlink?(state_file), "STATE_FILE_UNSAFE")
  state = YAML.safe_load_file(state_file, aliases: false)
  registry = YAML.safe_load_file(
    File.join(args[:control_checkout], "PROJECTS.yaml"), aliases: false
  )
  result = successor_state(state, registry, args)
  if result.nil?
    puts "PROVIDER_SUCCESSOR_PR=NOT_PREDECLARED"
  elsif args[:dry_run]
    puts result
  else
    prepare_pr(args, result)
  end
rescue HandoffError => error
  # Error messages are fixed non-secret admission codes only.
  warn "::warning::Provider succeeded; next-stage document PR not prepared: #{error.message}"
  exit 1
rescue KeyError, JSON::ParserError
  warn "::warning::Provider succeeded; next-stage document PR not prepared: UNVERIFIED_RESPONSE"
  exit 1
end
