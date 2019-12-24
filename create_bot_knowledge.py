#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2019 Francesco Murdaca
#
# This program is free software: you can redistribute it and / or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""Connect and store knowledge for the bots from GitHub."""

import logging
import os
import json

from typing import List, Tuple, Dict, Optional, Union, Set, Any, Sequence
from pathlib import Path

from github import Github, GithubObject, Issue, IssueComment, PullRequest, PullRequestReview, PaginatedList
from github.Repository import Repository

_LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_GITHUB_ACCESS_TOKEN = os.getenv("GITHUB_ACCESS_TOKEN")

ISSUE_KEYWORDS = {'close',
                  'closes',
                  'closed',
                  'fix',
                  'fixes',
                  'fixed',
                  'resolve',
                  'resolves',
                  'resolved'}


STANDALONE_LABELS = {'size'}


def connect_to_source(project: Tuple[str, str]) -> Repository:
    """Connect to GitHub.

    :param project: Tuple source repo and repo name.
    """
    # TODO: It should use only one library for source.

    # Connect using PyGitHub
    g = Github(_GITHUB_ACCESS_TOKEN)
    repo_name = project[1] + "/" + project[0]
    repo = g.get_repo(repo_name)

    return repo


def check_directory(knowledge_dir: Path):
    """Check if directory for bot knowledge exists. If not, create one."""
    if not knowledge_dir.exists():
        _LOGGER.info(
            "No knowledge from any repo has ever been created, creating new directory at %s" % knowledge_dir)
        os.makedirs(knowledge_dir)


def get_labeled_size(labels: List[str]) -> str:
    """Extract size label from list of labels.

    Size label is in form 'size/<SIZE>', where <SIZE> can be
    XS, S, L, etc...
    """
    for label in labels:
        if label.startswith('size'):
            return label.split('/')[1]


def get_non_standalone_labels(labels: List[str]):
    """Get non standalone labels by filtering them from all of the labels."""
    return [label for label in labels if label not in STANDALONE_LABELS]


def get_referenced_issues(pull_request: PullRequest) -> List[int]:
    """Scan all of the Pull Request comments and get referenced issues.

    Arguments:
        pull_request {PullRequest} -- Pull request for which the referenced
                                      issues are extracted

    Returns:
        List[int] -- IDs of referenced issues within the Pull Request.

    """
    issues_referenced = []
    for comment in pull_request.get_issue_comments():
        message = comment.body.split(' ')
        for idx, word in enumerate(message):
            if word.replace(':', '') in ISSUE_KEYWORDS:
                try:
                    _LOGGER.info('      ...found keyword referencing issue')
                    referenced_issue_number = message[idx+1]
                    assert(referenced_issue_number).startswith('https')
                    # last element of url is always the issue number
                    issues_referenced.append(
                        referenced_issue_number.split('/')[-1])
                    _LOGGER.info('      ...referenced issue number present')
                    # we assure that this was really referenced issue
                    # and not just a keyword without number
                except (IndexError, AssertionError) as e:
                    _LOGGER.info('      ...referenced issue number absent')
                    _LOGGER.debug(str(e))
    _LOGGER.debug('      referenced issues: %s' % issues_referenced)
    return issues_referenced


def get_only_new_entities(old_data: Dict[str, Any], new_data: PaginatedList) -> PaginatedList:
    """Get new entities (whether PRs or other Issues).

    The comparisson is made on IDs between previously collected
    entities and all currently present entities on GitHub.

    Arguments:PaginatedList
        old_data {Dict[str, Any]} -- previously collected knowledge
        new_data {PaginatedList} -- current entities present on GitHub
                          (acquired by GitHub API)

    Returns:
        List[PaginatedList] -- filtered new data without the old ones

    """
    old_knowledge_ids = [int(id) for id in old_data.keys()]
    _LOGGER.debug("Currently gathered ids %s" % old_knowledge_ids)

    new_knowledge_ids = [pr.number for pr in new_data]

    only_new_ids = set(new_knowledge_ids) - set(old_knowledge_ids)
    if len(only_new_ids) == 0:
        _LOGGER.info("No new knowledge found for update")
    else:
        _LOGGER.debug("New ids to be examined are %s" % only_new_ids)

    return [x for x in new_data if x.number in only_new_ids]


def load_previous_knowledge(repo_path: Path) -> Dict[str, Any]:
    """Load previously collected repo knowledge. If a repo was not inspected before, create its directory.

    Arguments:
        repo_path {Path} -- path of the inspected github repository

    Returns:
        Dict[str, Any] -- previusly collected knowledge.
                          Empty dict if the knowledge does not exist.

    """
    if not repo_path.exists() or os.path.getsize(repo_path) == 0:
        _LOGGER.info('No previous knowledge found for %s' %
                     os.path.basename(repo_path))
        return {}

    with open(repo_path, 'r') as f:
        data = json.load(f)
    results = data['results']
    _LOGGER.info('Found previous %s knowledge of size %d' %
                 (os.path.basename(repo_path), len(results)))
    return results


def save_knowledge(file_path: Path, data: Dict[str, Any]):
    """Save collected knowledge as json.

    The saved json contains one dictionary with single key 'results'
    under which the knowledge is stored.

    Arguments:
        file_path {Path} -- where the knowledge should be saved
        data {Dict[str, Any]} -- collected knowledge. Should be json compatible
    """
    results = {'results': data}

    with open(file_path, 'w') as f:
        json.dump(results, f)
    _LOGGER.info('Saved new knowledge file %s of size %d' %
                 (os.path.basename(file_path), len(data)))


def get_interactions(comments):
    """Get overall word count for comments per author."""
    interactions = {comment.user.login: 0 for comment in comments}
    for comment in comments:
        # we count by the num of words in comment
        interactions[comment.user.login] += len(comment.body.split(' '))
    return interactions


def store_issue(issue: Issue, data: Dict[str, Dict[str, Any]]):
    """Extract required information from issue and store it to the current data.

    This is targeted only for issues that are not Pull Requests.

    Arguments:
        issue {Issue} -- Issue (that is not PR).
        data {Dict[str, Union[str, int]])} -- Dictionary where the issue will be stored.

    """
    if issue.pull_request is not None:
        return  # we analyze issues and prs differentely

    created_at = issue.created_at.timestamp()
    closed_at = issue.closed_at.timestamp()
    time_to_close = closed_at - created_at

    labels = [label.name for label in issue.get_labels()]

    data[issue.number] = {
        "created_by": issue.user.login,
        "created_at": created_at,
        "closed_by": issue.closed_by.login,
        "closed_at": closed_at,
        "labels": get_non_standalone_labels(labels),
        "time_to_close": time_to_close,
        "interactions": get_interactions(issue.get_comments()),
    }

    # TODO: think about saving comments
    # would it be valuable?


def analyse_issues(project: Repository, project_knowledge: Path):
    """Analyse of every closed issue in repository.

    Arguments:
        project {Repository} -- currently the PyGithub lib is used because of its functionality
                                ogr unfortunatelly did not provide enough to properly analyze issues

        project_knowledge {Path} -- project directory where the issues knowledge will be stored

    """
    _LOGGER.info('-------------Issues (that are not PR) Analysis-------------')
    data_path = project_knowledge.joinpath('./issues.json')

    data = load_previous_knowledge(data_path)
    current_issues = [issue for issue in project.get_issues(
        state='closed') if issue.pull_request is None]
    new_issues = get_only_new_entities(data, current_issues)

    if len(new_issues) == 0:
        return

    for idx, issue in enumerate(new_issues, 1):
        _LOGGER.info("Analysing ISSUE no. %d/%d" % (idx, len(new_issues)))
        store_issue(issue, data)

    save_knowledge(data_path, data)


def extract_pullrequest_review_requests(pullrequest: PullRequest) -> List[str]:
    """Extract features from requested reviews of the PR.

    GitHub understands review requests rather as requested reviewers than actual
    requests.

    Arguments:
        pullrequest {PullRequest} -- PR of which we can extract review requests.

    Returns:
        List[str] -- list of logins of the requested reviewers

    """
    requested_users = pullrequest.get_review_requests()[0]

    extracted = []
    for user in requested_users:
        extracted.append(user.login)
    return extracted


def extract_pullrequest_reviews(pullrequest: PullRequest) -> Dict[str, Dict[str, Any]]:
    """Extract required features for each review from PR.

    Arguments:
        pullrequest {PullRequest} -- Pull Request from which the reviews will be extracted

    Returns:
        Dict[str, Dict[str, Any]] -- dictionary of extracted reviews. Each review is stored
                                     by its ID.

    """
    reviews = pullrequest.get_reviews()
    _LOGGER.info("  -num of reviews found: %d" % reviews.totalCount)

    results = {}
    for idx, review in enumerate(reviews, 1):
        _LOGGER.info("      -analysing review no. %d/%d" %
                     (idx, reviews.totalCount))
        results[review.id] = {
            "author": review.user.login,
            "words_count": len(review.body.split(' ')),
            "submitted_at": review.submitted_at.timestamp(),
            "state": review.state,
        }
    return results


def store_pullrequest(pull: PullRequest, results: Dict[str, Dict[str, Any]]):
    """Analyse pull request and save its desired features to results.

    Arguments:
        pull {PullRequest} -- PR that is going to be inspected and stored.
        results {Dict[str, Dict[str, Any]]} -- dictionary where all the currently
                                            PRs are stored and where the given PR
                                            will be stored.
    """
    commits = pull.commits
    # TODO: Use commits to extract information.
    # commits = [commit for commit in pull.get_commits()]

    created_at = pull.created_at.timestamp()
    closed_at = pull.closed_at.timestamp()

    # Get the review approvation if it exists
    # approvation = next((review for review in reviews if review.state == 'APPROVED'), None)
    # pr_approved = approvation.submitted_at.timestamp() if approvation is not None else None
    # pr_approved_by = pull.approved_by.name if approvation is not None else None
    # time_to_approve = pr_approved - created_at if approvation is not None else None

    merged_at = pull.merged_at.timestamp() if pull.merged_at is not None else None

    labels = [label.name for label in pull.get_labels()]

    results[str(pull.number)] = {
        "size": get_labeled_size(labels),
        "labels": get_non_standalone_labels(labels),
        "created_by": pull.user.login,
        "created_at": created_at,
        # "approved_at": pr_approved,
        # "approved_by": pr_approved_by,
        # "time_to_approve": time_to_approve,
        "closed_at": closed_at,
        "closed_by": pull.as_issue().closed_by.login,
        "time_to_close": closed_at - created_at,
        "merged_at": merged_at,
        "commits_number": commits,
        "referenced_issues": get_referenced_issues(pull),
        "interactions": get_interactions(pull.get_issue_comments()),
        "reviews": extract_pullrequest_reviews(pull),
        "requested_reviewers": extract_pullrequest_review_requests(pull),
    }


def analyse_pullrequests(project: Repository, project_knowledge: Path):
    """Analyse every closed pullrequest in repository.

    Arguments:
        project {Repository} -- currently the PyGithub lib is used because of its functionality
                                ogr unfortunatelly did not provide enough to properly analyze issues

        project_knowledge {Path} -- project directory where the issues knowledge will be stored
    """
    _LOGGER.info(
        '-------------Pull Requests Analysis (including its Reviews)-------------')

    pulls_data_path = project_knowledge.joinpath('./pull_requests.json')
    prev_pulls = load_previous_knowledge(pulls_data_path)

    current_pulls = project.get_pulls(state='closed')
    new_pulls = get_only_new_entities(prev_pulls, current_pulls)

    if len(new_pulls) == 0:
        return

    for idx, pullrequest in enumerate(new_pulls, 1):
        _LOGGER.info("Analysing PULL REQUEST no. %d/%d" %
                     (idx, len(new_pulls)))
        store_pullrequest(pullrequest, prev_pulls)
        _LOGGER.info('/n')
    save_knowledge(pulls_data_path, prev_pulls)


def analyse_projects(projects: List[Tuple[str, str]]) -> None:
    """Run Issues (that are not PRs), PRs, PR Reviews analysis on specified projectws.

    Arguments:
        projects {List[Tuple[str, str]]} -- one tuple should be in format (repository_name, project_name)
    """
    path = Path.cwd().joinpath('./Bot_Knowledge')
    for project in projects:
        github_repo = connect_to_source(project=project)

        project_path = path.joinpath('./' + github_repo.full_name)
        check_directory(project_path)

        analyse_issues(github_repo, project_path)
        analyse_pullrequests(github_repo, project_path)