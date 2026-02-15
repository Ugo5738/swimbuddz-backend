# Branching & Release Flow

## Branch Roles

- **main**: production. Only release-ready code. Deploys to prod.
- **staging**: pre-prod validation. Mirrors prod, no direct prod impact.
- **develop**: integration branch for ongoing work. CI only, no deploys.

## Workflow

1. Feature branches → merge into **develop**
2. When stable, merge **develop → staging** for staging validation
3. After validation, merge **staging → main** to release to prod

## Hotfixes

1. Branch off **main**
2. Merge hotfix → **main** (deploys)
3. Back-merge hotfix → **staging** and **develop**

## CI/CD

- CI runs on **develop**, **staging**, **main**
- Deploys only on **main** (prod)
- **staging** builds images; deploy is currently disabled
