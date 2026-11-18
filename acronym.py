from db import acronym_collection, banned_phrase_collection
import random


def normalize_reserved_acronym(value):
  return "".join(char for char in value.upper() if char.isalnum())


BAN_TYPE_PHRASE = "phrase"
BAN_TYPE_ACRO = "acro"


def validate_generated_acronym(generated_acronym, reserved_acronyms=None, guild_id=None):
  normalized = normalize_reserved_acronym(generated_acronym)
  if reserved_acronyms and normalized in reserved_acronyms:
    raise ValueError(f"You can't create the command **{generated_acronym}** as an acronym.")
  if guild_id is not None and is_acro_banned(guild_id, normalized):
    raise ValueError(f"The acronym **{normalized}** is banned and can't be created.")

def load_acronym_database():
  """Load all acronyms from MongoDB into memory (optional, can query directly)."""
  try:
    acronyms = list(acronym_collection.find({}))
    print(f"Loaded {len(acronyms)} acronyms from database")
  except Exception as e:
    print(f"Error loading acronym database: {e}")


def save_acronym_database():
  """MongoDB automatically persists data, but this is kept for API compatibility."""
  try:
    print("Acronym database is auto-persisted in MongoDB")
  except Exception as e:
    print(f"Error in save_acronym_database: {e}")


def acronym(guild_id, phrase, author_id=None, reserved_acronyms=None):
  guild_id = str(guild_id)
  normalized = phrase.strip().lower()
  alphanumerical_words = ["".join(char for char in word if char.isalnum()) for word in normalized.split()]
  alphanumerical_words = [word for word in alphanumerical_words if word]
  if len("".join(alphanumerical_words)) < 4:
    raise ValueError("Phrase must be at least 4 characters long.")
  if is_phrase_banned(guild_id, normalized):
    raise ValueError(f"The phrase **{normalized}** is banned and can't be acro'd.")
  if len(alphanumerical_words) == 1:
    return word_acronym(guild_id, normalized, alphanumerical_words[0], author_id, reserved_acronyms)
  return phrase_acronym(guild_id, normalized, alphanumerical_words, author_id, reserved_acronyms)


def word_acronym(guild_id, phrase, word, author_id=None, reserved_acronyms=None):
  if len(word) < 4:
    raise ValueError("Word must be at least 4 characters long.")

  generated_acronym = word[-(len(word) // 2):].upper()
  validate_generated_acronym(generated_acronym, reserved_acronyms, guild_id)
  existing = acronym_collection.find_one({'guild_id': guild_id, 'phrase': phrase})
  if existing:
    raise ValueError(f"This acronym has already been added: **{existing['phrase']}** → {existing['acronym']}")
  doc = {'guild_id': guild_id, 'phrase': phrase, 'acronym': generated_acronym}
  if author_id is not None:
    doc['author_id'] = str(author_id)
  acronym_collection.insert_one(doc)
  return generated_acronym


def phrase_acronym(guild_id, phrase, words, author_id=None, reserved_acronyms=None):
  parts = []
  for word in words:
    i = 0
    while i < len(word) and not word[i].isalpha():
      i += 1
    prefix = word[:i]
    first_alpha = word[i].upper() if i < len(word) else ""
    parts.append(prefix + first_alpha)

  generated_acronym = "".join(parts)

  if not any(c.isalpha() for c in generated_acronym):
    raise ValueError("You can't acro a phrase of only numbers.")
  validate_generated_acronym(generated_acronym, reserved_acronyms, guild_id)

  existing = acronym_collection.find_one({'guild_id': guild_id, 'phrase': phrase})
  if existing:
    raise ValueError(f"This acronym has already been added: **{existing['phrase']}** → {existing['acronym']}")
  doc = {'guild_id': guild_id, 'phrase': phrase, 'acronym': generated_acronym}
  if author_id is not None:
    doc['author_id'] = str(author_id)
  acronym_collection.insert_one(doc)
  return generated_acronym


def get_matching_acronym(guild_id, content_lower):
  """Find all matching acronym phrases in content and return a random one."""
  try:
    all_acronyms = list(acronym_collection.find({'guild_id': str(guild_id)}))
    matches = [entry['acronym'] for entry in all_acronyms if entry['phrase'] in content_lower]
    if not matches:
      return None
    return random.choice(matches)
  except Exception as e:
    print(f"Error getting matching acronym: {e}")
    return None


def check_unacro_permission(doc, requester_id, blouis_id):
  requester_id = str(requester_id)
  if requester_id == str(blouis_id):
    return
  author_id = doc.get('author_id')
  if not author_id:
    raise ValueError("Can't unacro unclaimed acronyms")
  if requester_id != str(author_id):
    raise ValueError("Only the person who claimed this acronym can remove it.")


def unacronym(guild_id, phrase, requester_id, blouis_id):
  guild_id = str(guild_id)
  normalized = phrase.strip().lower()
  if not normalized:
    raise ValueError("Word or phrase cannot be empty.")

  doc = acronym_collection.find_one({'guild_id': guild_id, 'phrase': normalized})
  if not doc:
    return False

  check_unacro_permission(doc, requester_id, blouis_id)

  result = acronym_collection.delete_one({'_id': doc['_id']})
  return result.deleted_count > 0


def unacronym_by_acronym(guild_id, acronym_str, requester_id, blouis_id):
  guild_id = str(guild_id)
  normalized = acronym_str.strip().upper()
  if not normalized:
    raise ValueError("Acronym cannot be empty.")

  doc = acronym_collection.find_one({'guild_id': guild_id, 'acronym': normalized})
  if not doc:
    return False

  check_unacro_permission(doc, requester_id, blouis_id)

  result = acronym_collection.delete_one({'_id': doc['_id']})
  return result.deleted_count > 0


def unacronym_all_by_author(guild_id, author_id):
  guild_id = str(guild_id)
  normalized_author_id = str(author_id).strip()
  if not normalized_author_id:
    raise ValueError("Author ID cannot be empty.")

  result = acronym_collection.delete_many({'guild_id': guild_id, 'author_id': normalized_author_id})
  return result.deleted_count


def _normalize_ban_value(value, ban_type):
  if ban_type == BAN_TYPE_ACRO:
    return normalize_reserved_acronym(value)
  if ban_type == BAN_TYPE_PHRASE:
    return value.strip().lower()
  raise ValueError("Ban type must be 'phrase' or 'acro'.")


def _ban_query(guild_id, normalized, ban_type):
  query = {'guild_id': str(guild_id), 'phrase': normalized}
  if ban_type == BAN_TYPE_PHRASE:
    # Legacy entries have no type field and are phrases.
    query['type'] = {'$in': [BAN_TYPE_PHRASE, None]}
  else:
    query['type'] = ban_type
  return query


def is_phrase_banned(guild_id, phrase):
  normalized = _normalize_ban_value(phrase, BAN_TYPE_PHRASE)
  return banned_phrase_collection.find_one(_ban_query(guild_id, normalized, BAN_TYPE_PHRASE)) is not None


def is_acro_banned(guild_id, acro):
  normalized = _normalize_ban_value(acro, BAN_TYPE_ACRO)
  return banned_phrase_collection.find_one(_ban_query(guild_id, normalized, BAN_TYPE_ACRO)) is not None


def ban_phrase(guild_id, phrase, ban_type=BAN_TYPE_PHRASE):
  guild_id = str(guild_id)
  normalized = _normalize_ban_value(phrase, ban_type)
  if not normalized:
    raise ValueError("Phrase cannot be empty.")

  if banned_phrase_collection.find_one(_ban_query(guild_id, normalized, ban_type)) is not None:
    raise ValueError(f"The {ban_type} **{normalized}** is already banned.")

  if ban_type == BAN_TYPE_ACRO:
    existing_docs = list(acronym_collection.find({'guild_id': guild_id, 'acronym': normalized}))
  else:
    existing_docs = list(acronym_collection.find({'guild_id': guild_id, 'phrase': normalized}))

  ban_doc = {'guild_id': guild_id, 'phrase': normalized, 'type': ban_type}
  author_id = next((doc['author_id'] for doc in existing_docs if doc.get('author_id') is not None), None)
  if author_id is not None:
    ban_doc['author_id'] = author_id

  banned_phrase_collection.insert_one(ban_doc)
  existing_removed = len(existing_docs) > 0
  if existing_removed:
    acronym_collection.delete_many({'_id': {'$in': [doc['_id'] for doc in existing_docs]}})
  return normalized, existing_removed


def unban_phrase(guild_id, phrase, ban_type=BAN_TYPE_PHRASE):
  normalized = _normalize_ban_value(phrase, ban_type)
  if not normalized:
    raise ValueError("Phrase cannot be empty.")

  result = banned_phrase_collection.delete_one(_ban_query(guild_id, normalized, ban_type))
  return result.deleted_count > 0


def list_banned_phrases(guild_id):
  """Return all (phrase, author_id, type) tuples banned for this guild, sorted by type then phrase."""
  guild_id = str(guild_id)
  results = list(banned_phrase_collection.find(
    {'guild_id': guild_id},
    {'_id': 0, 'phrase': 1, 'author_id': 1, 'type': 1}
  ))
  entries = [(doc['phrase'], doc.get('author_id'), doc.get('type') or BAN_TYPE_PHRASE) for doc in results]
  entries.sort(key=lambda entry: (entry[2], entry[0]))
  return entries


def get_ban_leaderboard(guild_id):
  """Return [(author_id, ban_count), ...] sorted by most banned phrases claimed by that author."""
  guild_id = str(guild_id)
  results = list(banned_phrase_collection.find(
    {'guild_id': guild_id, 'author_id': {'$ne': None}},
    {'_id': 0, 'author_id': 1}
  ))

  counts = {}
  for doc in results:
    author_id = doc.get('author_id')
    if not author_id:
      continue
    counts[author_id] = counts.get(author_id, 0) + 1

  return sorted(counts.items(), key=lambda item: item[1], reverse=True)
