variable "project_id" { type = string }
variable "region" { type = string }
variable "zone" { type = string }
variable "instance_name" { type = string }

variable "gh_app_id" {
  type    = string
  default = ""
}
variable "gh_app_installation_id" {
  type    = string
  default = ""
}
variable "gh_app_private_key" {
  type    = string
  default = ""
}
variable "iap_group_email" {
  type = string
}
