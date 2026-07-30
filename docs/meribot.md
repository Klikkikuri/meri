# Klikkikuri Meri Bot

This document provides information about the crawler and bot provided by Klikkikuri meri

## Important Notice: Independence & Non-Affiliation

**This bot, instance, and its underlying infrastructure are operated independently by a third party.**

This instance of the bot is **not** operated, owned, endorsed, maintained, or officially affiliated with the official Klikkikuri organization or its repository maintainers (found at [github.com/Klikkikuri](https://github.com/Klikkikuri)). Any questions, issues, or requests regarding this crawler should be directed to this specific independent deployment's operator, not the Klikkikuri project.

## Overview

The Klikkikuri Meri bot is an automated content fetching service used to fetch public news articles for headline analysis and processing, mirroring the architecture found in the [Klikkikuri/meri](https://github.com/Klikkikuri/meri) repository.

## Identification

### User-Agent String

When visiting or crawling websites, the bot identifies itself using an HTTP `User-Agent` string similar to the following:

```text
Mozilla/5.0 (compatible; Klikkikuri-Meri-Bot/1.0.0; +https://github.com/Klikkikuri/meri/docs/meribot.md)
```

Specific identifier, bot name, version numbers, or contact tokens may vary depending on the configuration of independent deployment.

### How to Block the Bot

The bot is designed to respect standard `robots.txt` exclusion protocols. If you wish to prevent this independent crawler from accessing your web server, you can block its User-Agent string via your web server configuration or site's `robots.txt`:

```text
User-agent: Klikkikuri-Meri-Bot
Disallow: /
```

The exact User-Agent string may vary depending on the deployment, so please check the actual requests made to your server for the correct identifier.
