#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(readr)
  library(dplyr)
  library(tidygeocoder)
})

args <- commandArgs(trailingOnly = TRUE)

get_arg <- function(flag, default = NULL) {
  idx <- match(flag, args)
  if (is.na(idx) || idx == length(args)) {
    return(default)
  }
  args[[idx + 1]]
}

has_flag <- function(flag) {
  flag %in% args
}

input_csv <- get_arg("--input", "paper/Appendix_A_Supplementary_data/transcriptome_data_20260323.csv")
output_csv <- get_arg("--output", "outputs/geographic_backfill/transcriptome_data_20260323.geographic_backfilled.csv")
review_csv <- get_arg("--review", "outputs/geographic_backfill/geographic_coordinate_backfill_review.csv")
cache_csv <- get_arg("--cache", "outputs/geographic_backfill/geocode_cache.csv")
conflicts_csv <- get_arg("--conflicts", "outputs/geographic_backfill/geographic_coordinate_mapping_conflicts.csv")
enable_geocode <- has_flag("--geocode")
allow_broad <- has_flag("--allow-broad")
target_locations_arg <- get_arg("--locations", "")
target_locations <- trimws(unlist(strsplit(target_locations_arg, "\\|", fixed = FALSE)))
target_locations <- target_locations[target_locations != ""]
max_geocode <- as.integer(get_arg("--max-geocode", "50"))
if (is.na(max_geocode) || max_geocode < 0) {
  stop("--max-geocode must be a non-negative integer")
}

dir.create(dirname(output_csv), recursive = TRUE, showWarnings = FALSE)
dir.create(dirname(review_csv), recursive = TRUE, showWarnings = FALSE)
dir.create(dirname(cache_csv), recursive = TRUE, showWarnings = FALSE)
dir.create(dirname(conflicts_csv), recursive = TRUE, showWarnings = FALSE)

is_missing <- function(x) {
  x <- trimws(as.character(x))
  is.na(x) | x == "" | tolower(x) %in% c("na", "n/a", "nan", "missing", "not available")
}

normalize_coord <- function(x) {
  x <- trimws(as.character(x))
  x[is_missing(x)] <- NA_character_
  x <- gsub("[;|]", ",", x)
  x <- gsub("\\s+", " ", x)
  x <- gsub("\\s*,\\s*", ", ", x)
  x
}

parse_coord <- function(x) {
  x <- normalize_coord(x)
  out <- data.frame(lat = rep(NA_real_, length(x)), lon = rep(NA_real_, length(x)))
  for (i in seq_along(x)) {
    if (is.na(x[[i]])) next
    nums <- regmatches(x[[i]], gregexpr("-?\\d+(?:\\.\\d+)?", x[[i]], perl = TRUE))[[1]]
    if (length(nums) < 2) next
    lat <- suppressWarnings(as.numeric(nums[[1]]))
    lon <- suppressWarnings(as.numeric(nums[[2]]))
    if (!is.na(lat) && !is.na(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
      out$lat[[i]] <- lat
      out$lon[[i]] <- lon
    }
  }
  out
}

format_coord <- function(lat, lon) {
  ifelse(is.na(lat) | is.na(lon), NA_character_, sprintf("%.6f, %.6f", lat, lon))
}

clean_location <- function(x) {
  x <- trimws(as.character(x))
  x[is_missing(x)] <- NA_character_
  x <- gsub("\\s+", " ", x)
  x
}

location_to_query <- function(location) {
  if (is.na(location)) return(NA_character_)
  parts <- strsplit(location, ":", fixed = TRUE)[[1]]
  parts <- trimws(parts)
  if (length(parts) >= 2) {
    country <- parts[[1]]
    place <- paste(parts[-1], collapse = ", ")
    return(paste(place, country, sep = ", "))
  }
  location
}

is_broad_location <- function(location) {
  if (is.na(location)) return(TRUE)
  !grepl("[:,]", location) && length(strsplit(location, "\\s+")[[1]]) <= 3
}

read_cache <- function(path) {
  if (!file.exists(path)) {
    return(tibble(
      location = character(),
      query = character(),
      latitude = numeric(),
      longitude = numeric(),
      coordinates = character(),
      display_name = character(),
      geocode_status = character()
    ))
  }
  read_csv(path, show_col_types = FALSE)
}

write_cache <- function(cache, path) {
  cache |>
    distinct(location, .keep_all = TRUE) |>
    arrange(location) |>
    write_csv(path)
}

df <- read_csv(input_csv, show_col_types = FALSE, na = c("", "NA", "N/A", "NaN"))
required <- c("Geographic location", "Geographic coordinates")
missing_cols <- setdiff(required, names(df))
if (length(missing_cols) > 0) {
  stop("Missing expected columns: ", paste(missing_cols, collapse = ", "))
}

loc <- clean_location(df[["Geographic location"]])
coord <- normalize_coord(df[["Geographic coordinates"]])
parsed <- parse_coord(coord)
valid_coord <- !is.na(parsed$lat) & !is.na(parsed$lon)
needs_backfill <- !is.na(loc) & !valid_coord

known_pairs <- tibble(location = loc[valid_coord], coordinates = coord[valid_coord]) |>
  filter(!is.na(location), !is.na(coordinates)) |>
  count(location, coordinates, name = "n") |>
  group_by(location) |>
  arrange(desc(n), coordinates, .by_group = TRUE) |>
  mutate(rank = row_number(), n_coordinates_for_location = n()) |>
  ungroup()

conflicts <- known_pairs |>
  filter(n_coordinates_for_location > 1)
write_csv(conflicts, conflicts_csv)

location_map <- known_pairs |>
  filter(rank == 1) |>
  transmute(location, mapped_coordinates = coordinates, source = "same_location_in_input")
if (!allow_broad) {
  location_map <- location_map |>
    filter(!vapply(location, is_broad_location, logical(1)))
}

backfill <- tibble(row_id = seq_len(nrow(df)), location = loc, original_coordinates = coord) |>
  left_join(location_map, by = "location") |>
  mutate(
    proposed_coordinates = if_else(needs_backfill & !is.na(mapped_coordinates), mapped_coordinates, NA_character_),
    backfill_source = if_else(!is.na(proposed_coordinates), source, NA_character_)
  ) |>
  select(row_id, location, original_coordinates, proposed_coordinates, backfill_source)

cache <- read_cache(cache_csv)

if (enable_geocode) {
  already_cached <- cache$location
  geocode_targets <- backfill |>
    filter(is.na(proposed_coordinates), !is.na(location)) |>
    distinct(location) |>
    filter(!location %in% already_cached)
  if (length(target_locations) > 0) {
    geocode_targets <- geocode_targets |>
      filter(location %in% target_locations)
  }
  if (!allow_broad) {
    geocode_targets <- geocode_targets |>
      filter(!vapply(location, is_broad_location, logical(1)))
  }
  geocode_targets <- geocode_targets |>
    slice_head(n = max_geocode)

  if (nrow(geocode_targets) > 0) {
    message("Geocoding ", nrow(geocode_targets), " unique locations with tidygeocoder/Nominatim...")
    geocode_input <- geocode_targets |>
      mutate(query = vapply(location, location_to_query, character(1)))
    geocoded <- tryCatch(
      geocode_input |>
        tidygeocoder::geocode(
          address = query,
          method = "osm",
          lat = "latitude",
          long = "longitude",
          full_results = TRUE,
          limit = 1,
          min_time = 1.1,
          quiet = FALSE
        ),
      error = function(e) {
        message("tidygeocoder failed: ", e$message)
        NULL
      }
    )
    if (is.null(geocoded)) {
      new_cache <- geocode_input |>
        transmute(
          location,
          query,
          latitude = NA_real_,
          longitude = NA_real_,
          coordinates = NA_character_,
          display_name = NA_character_,
          geocode_status = "error"
        )
    } else {
      if (!"display_name" %in% names(geocoded)) {
        geocoded$display_name <- NA_character_
      }
      new_cache <- geocoded |>
        transmute(
          location,
          query,
          latitude,
          longitude,
          coordinates = format_coord(latitude, longitude),
          display_name,
          geocode_status = if_else(!is.na(latitude) & !is.na(longitude), "ok", "not_found")
        )
    }
    cache <- bind_rows(cache, new_cache)
    write_cache(cache, cache_csv)
  }

  geocode_map <- cache |>
    filter(geocode_status == "ok", !is.na(coordinates)) |>
    transmute(
      location,
      geocoded_coordinates = coordinates,
      geocode_query = query,
      geocode_display_name = display_name
    )

  backfill <- backfill |>
    left_join(geocode_map, by = "location") |>
    mutate(
      proposed_coordinates = coalesce(proposed_coordinates, geocoded_coordinates),
      backfill_source = case_when(
        !is.na(backfill_source) ~ backfill_source,
        !is.na(geocoded_coordinates) ~ "nominatim",
        TRUE ~ NA_character_
      )
    )
} else {
  backfill <- backfill |>
    mutate(geocode_query = NA_character_, geocode_display_name = NA_character_)
}

review <- backfill |>
  filter(needs_backfill) |>
  mutate(will_fill = !is.na(proposed_coordinates)) |>
  arrange(desc(will_fill), location, row_id)

write_csv(review, review_csv)

out <- df
fill_rows <- review$row_id[review$will_fill]
out[["Geographic coordinates"]][fill_rows] <- review$proposed_coordinates[review$will_fill]
write_csv(out, output_csv)

message("Rows with location present and coordinates missing: ", sum(needs_backfill))
message("Rows proposed for coordinate fill: ", length(fill_rows))
message("Wrote output CSV: ", normalizePath(output_csv))
message("Wrote review CSV: ", normalizePath(review_csv))
message("Wrote conflicts CSV: ", normalizePath(conflicts_csv))
if (enable_geocode) {
  message("Wrote geocode cache: ", normalizePath(cache_csv))
} else {
  message("External geocoding not used. Add --geocode to query Nominatim with cache and rate limiting.")
}
