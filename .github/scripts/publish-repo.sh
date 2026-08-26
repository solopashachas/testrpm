#!/usr/bin/bash
set -euo pipefail

inventory="state/${logical_repository}/inventory.tsv"
mkdir -p incoming/rpms metadata-fragments "state/${logical_repository}"
touch "${inventory}"

rpm_safe_name() {
  local name="$1"
  name="${name//\~/_}"
  printf "%s\n" "${name//\^/_}"
}

rpm_matches_releasever() {
  local rpm_release
  rpm_release="$(rpm -qp --qf '%{RELEASE}' "$1")"
  [[ "${rpm_release}" =~ \.fc${releasever}([._]|$) ]]
}

case "${1:?stage is required}" in
  discover)
    : > discovered.tsv
    mapfile -t packages < <(
      find plasma related frameworks -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -u
    )
    for package in "${packages[@]}"; do
      image="ghcr.io/${GITHUB_REPOSITORY}/${package,,}"
      mapfile -t tags < <(
        oras repo tags --format json "${image}" 2>/dev/null |
          jq -r '.tags[]?' |
          grep -E -- "-${branch}-${releasever}$" |
          grep -v -E '^latest-' || true
      )
      # Seed repositories from images created before scoped immutable tags existed.
      if oras manifest fetch "${image}:latest-${branch}-${releasever}" >/dev/null 2>&1; then
        tags+=("latest-${branch}-${releasever}")
      fi
      for tag in "${tags[@]}"; do
        descriptor="$(oras manifest fetch --descriptor "${image}:${tag}")"
        digest="$(jq -r '.digest' <<< "${descriptor}")"
        printf '%s\t%s\t%s\n' "${package}" "${image}@${digest}" "${digest}" >> discovered.tsv
      done
    done
    sort -u -o discovered.tsv discovered.tsv
    : > new-manifests.tsv
    while IFS=$'\t' read -r package reference digest; do
      awk -F '\t' -v digest="${digest}" '$5 == digest { found=1 } END { exit !found }' "${inventory}" ||
        printf '%s\t%s\t%s\n' "${package}" "${reference}" "${digest}" >> new-manifests.tsv
    done < discovered.tsv
    ;;

  download)
    mkdir -p incoming/manifests incoming/rpms
    : > incoming/candidates.tsv
    while IFS=$'\t' read -r package reference digest; do
      [[ -n "${reference}" ]] || continue
      destination="incoming/manifests/${digest#sha256:}"
      mkdir -p "${destination}"
      oras pull "${reference}" -o "${destination}"
      while IFS= read -r -d '' rpm_file; do
        rpm_name="$(rpm_safe_name "$(basename "${rpm_file}")")"
        if [[ -e "incoming/rpms/${rpm_name}" ]] && ! cmp -s "${rpm_file}" "incoming/rpms/${rpm_name}"; then
          echo "Conflicting RPM assets have the same name: ${rpm_name}" >&2
          exit 1
        fi
        cp -n "${rpm_file}" "incoming/rpms/${rpm_name}"
        printf '%s\t%s\t%s\n' "${rpm_name}" "${reference}" "${digest}" >> incoming/candidates.tsv
      done < <(find "${destination}" -type f -name '*.rpm' -print0)
    done < new-manifests.tsv

    sort -u -o incoming/candidates.tsv incoming/candidates.tsv
    : > incoming/new-rpms.tsv
    while IFS=$'\t' read -r rpm_name reference digest; do
      [[ -n "${rpm_name}" ]] || continue
      if ! rpm_matches_releasever "incoming/rpms/${rpm_name}"; then
        echo "Skipping ${rpm_name}: not built for Fedora ${releasever}"
        continue
      fi
      if ! awk -F '\t' -v name="${rpm_name}" '$1 == name { found=1 } END { exit !found }' "${inventory}"; then
        kind=packages
        [[ "${rpm_name}" == *debuginfo-*.rpm || "${rpm_name}" == *debugsource-*.rpm ]] && kind=debuginfo
        printf '%s\t%s\t%s\t%s\n' "${rpm_name}" "${kind}" "${reference}" "${digest}" >> incoming/new-rpms.tsv
      fi
    done < incoming/candidates.tsv
    sort -u -o incoming/new-rpms.tsv incoming/new-rpms.tsv
    ;;

  bootstrap)
    touch incoming/new-rpms.tsv
    if [[ -s "${inventory}" ]]; then exit 0; fi
    legacy="${branch}"
    [[ "${testing}" == true ]] && legacy+="-testing"
    for suffix in '' '-debuginfo'; do
      tag="${legacy}${suffix}"
      kind=packages
      [[ -n "${suffix}" ]] && kind=debuginfo
      gh release view "${tag}" -R "${GITHUB_REPOSITORY}" >/dev/null 2>&1 || continue
      mkdir -p "incoming/legacy/${kind}"
      gh release download "${tag}" -R "${GITHUB_REPOSITORY}" --pattern '*.rpm' \
        --dir "incoming/legacy/${kind}"
      while IFS= read -r -d '' rpm_file; do
        rpm_matches_releasever "${rpm_file}" || continue
        rpm_name="$(rpm_safe_name "$(basename "${rpm_file}")")"
        if awk -F '\t' -v name="${rpm_name}" '$1 == name { found=1 } END { exit !found }' incoming/new-rpms.tsv; then
          continue
        fi
        cp -n "${rpm_file}" "incoming/rpms/${rpm_name}"
        printf '%s\t%s\tlegacy:%s\tlegacy:%s\n' "${rpm_name}" "${kind}" "${tag}" "${tag}" \
          >> incoming/new-rpms.tsv
      done < <(find "incoming/legacy/${kind}" -type f -name '*.rpm' -print0)
    done
    sort -u -o incoming/new-rpms.tsv incoming/new-rpms.tsv
    ;;

  assign)
    cp "${inventory}" "${inventory}.next"
    : > incoming/assignments.tsv
    assign_kind() {
      local kind="$1" repository_name="$2" bucket count rpm_name row_kind reference digest tag
      bucket="$(awk -F '\t' -v kind="${kind}" '$2 == kind { print $3 }' "${inventory}.next" | sort -n | tail -1)"
      bucket="${bucket:-1}"
      count="$(awk -F '\t' -v kind="${kind}" -v bucket="${bucket}" \
        '$2 == kind && $3 == bucket { n++ } END { print n+0 }' "${inventory}.next")"
      while IFS=$'\t' read -r rpm_name row_kind reference digest; do
        [[ "${row_kind}" == "${kind}" ]] || continue
        if (( count >= MAX_ASSETS_PER_RELEASE )); then bucket=$((bucket + 1)); count=0; fi
        tag="$(printf '%s-rpm-%04d' "${repository_name}" "${bucket}")"
        printf '%s\t%s\t%d\t%s\t%s\n' "${rpm_name}" "${kind}" "${bucket}" "${tag}" "${digest}" \
          >> "${inventory}.next"
        printf '%s\t%s\t%d\t%s\t%s\n' "${rpm_name}" "${kind}" "${bucket}" "${tag}" "${reference}" \
          >> incoming/assignments.tsv
        count=$((count + 1))
      done < incoming/new-rpms.tsv
    }
    assign_kind packages "${logical_repository}"
    assign_kind debuginfo "${debug_repository}"
    sort -u -o "${inventory}.next" "${inventory}.next"
    ;;

  upload)
    mapfile -t tags < <(cut -f4 incoming/assignments.tsv | sort -u)
    for tag in "${tags[@]}"; do
      [[ -n "${tag}" ]] || continue
      gh release view "${tag}" -R "${GITHUB_REPOSITORY}" >/dev/null 2>&1 ||
        gh release create "${tag}" -R "${GITHUB_REPOSITORY}" --title "${tag}" \
          --notes "RPM storage bucket for logical repository ${logical_repository}."
      gh release view "${tag}" -R "${GITHUB_REPOSITORY}" --json assets \
        -q '.assets[].name' > "incoming/${tag}.assets"
      while IFS= read -r file; do
        name="$(basename "${file}")"
        grep -Fqx "${name}" "incoming/${tag}.assets" && continue
        gh release upload "${tag}" "${file}" -R "${GITHUB_REPOSITORY}"
      done < <(awk -F '\t' -v tag="${tag}" '$4 == tag { print "incoming/rpms/" $1 }' incoming/assignments.tsv)
    done
    # Only commit bucket membership after all uploads succeed.
    mv "${inventory}.next" "${inventory}"
    ;;

  metadata)
    generate_repository() {
      local kind="$1" output="$2" old="repo/${output}" fragment tag rpm_name
      local -a sources=()
      [[ -f "${old}/repodata/repomd.xml" ]] && sources+=(--repo "${old}")
      mapfile -t tags < <(awk -F '\t' -v kind="${kind}" '$2 == kind { print $4 }' \
        incoming/assignments.tsv | sort -u)
      for tag in "${tags[@]}"; do
        [[ -n "${tag}" ]] || continue
        fragment="metadata-fragments/${tag}"
        mkdir -p "${fragment}"
        while IFS= read -r rpm_name; do
          ln -f "incoming/rpms/${rpm_name}" "${fragment}/${rpm_name}"
        done < <(awk -F '\t' -v tag="${tag}" '$4 == tag { print $1 }' incoming/assignments.tsv)
        createrepo_c --baseurl "https://github.com/${GITHUB_REPOSITORY}/releases/download/${tag}/" "${fragment}"
        sources+=(--repo "${fragment}")
      done
      if (( ${#sources[@]} == 0 )); then createrepo_c "${old}"; return; fi
      if (( ${#tags[@]} == 0 )); then return; fi
      rm -rf "merged-${output}"
      mergerepo_c --all -o "merged-${output}" "${sources[@]}"
      rm -rf "${old}/repodata"
      mv "merged-${output}/repodata" "${old}/repodata"
    }
    generate_repository packages "${logical_repository}"
    generate_repository debuginfo "${debug_repository}"
    dnf -q --repofrompath="logical,repo/${logical_repository}" --repo=logical rq \
      --qf '%{source_name}-%{version}-%{release}\n' | sort -u > "repo/${logical_repository}/packages.txt"
    ;;

  repofile)
    mkdir -p "repo/${normal_repository}"
    file="repo/${normal_repository}/${REPOSITORY}-${branch}-${releasever}.repo"
    emit_repo() {
      local id="$1" description="$2" enabled="$3"
      cat <<EOF
[${REPOSITORY}-github:${id}]
name=${GITHUB_REPOSITORY} (GitHub) - ${description}
baseurl=https://${REPOSITORY_OWNER}.github.io/${REPOSITORY}/${id}/
type=rpm-md
skip_if_unavailable=True
gpgcheck=1
gpgkey=https://raw.githubusercontent.com/solopashachas/testrpm/refs/heads/unstable/RPM-GPG-KEY-solopashachas
repo_gpgcheck=0
enabled=${enabled}
enabled_metadata=${enabled}
metadata_expire=6h

EOF
    }
    {
      emit_repo "${normal_repository}" "${branch} Fedora ${releasever}" 1
      emit_repo "${normal_repository}-testing" "${branch} Fedora ${releasever} - testing" 0
      emit_repo "${normal_repository}-debuginfo" "${branch} Fedora ${releasever} - debuginfo" 0
      emit_repo "${normal_repository}-testing-debuginfo" "${branch} Fedora ${releasever} - testing - debuginfo" 0
    } > "${file}"
    ;;

  validate)
    test -s "repo/${logical_repository}/repodata/repomd.xml"
    test -s "repo/${debug_repository}/repodata/repomd.xml"
    : > primary.xml
    for directory in "repo/${logical_repository}/repodata" "repo/${debug_repository}/repodata"; do
      metadata="$(find "${directory}" -name '*primary.xml.*' -print -quit)"
      [[ -n "${metadata}" ]] || continue
      case "${metadata}" in
        *.gz) gzip -cd "${metadata}" ;; *.zst) zstd -qdc "${metadata}" ;;
        *.bz2) bzip2 -cd "${metadata}" ;; *.xz) xz -cd "${metadata}" ;; *) cat "${metadata}" ;;
      esac >> primary.xml
    done
    while IFS=$'\t' read -r rpm_name _; do
      [[ -n "${rpm_name}" ]] || continue
      grep -Fq "href=\"${rpm_name}\"" primary.xml || {
        echo "Metadata is missing ${rpm_name}" >&2; exit 1;
      }
    done < "${inventory}"
    ;;

  *) echo "Unknown stage: $1" >&2; exit 2 ;;
esac
